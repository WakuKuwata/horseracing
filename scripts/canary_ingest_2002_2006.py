"""canary: 2002-2006 の 5 年を **後方 backfill 安全** に取り込む。

なぜ専用スクリプトか
--------------------
本番の `upsert_core` は master(`horses`/`jockeys`/`trainers`)も `on_conflict_do_update` する。
古い年を後から入れると **既存マスタが古い値で上書きされる**。実測したところ:

  重複 6,176 頭のうち 名前/父/母父/生年/系統 は **不一致 0**
  だが **馬主が 383 頭(6.2%)で違う**(実際の所有権移転)/ 生産者は 0

`horses.owner_name` は履歴を持たない単一の現在値なので、後方 backfill で古い馬主が勝つと
その馬の 2007+ レースの `asof_owner_win_rate` が変わる。**後方 backfill では既存マスタを
更新しない**のが正しい規則。

そこで master は `on_conflict_do_nothing`、`races`/`race_horses`/`race_results` は通常の
upsert(新規行しか無いので実質 insert)にする。

**このスクリプトは特徴に影響しない。** `features/loader.py` が
`race_date >= INGEST_SCOPE_START(=2007-01-01)` でフィルタしているので、
定数を変えるまで 2002-2006 は特徴ビルドに一切現れない(Phase 2 で別途変更する)。

検証(fail-closed)
-----------------
取込の前後で **既存の horses 行の checksum が一致すること**を検査する。1 行でも変われば異常。

ロールバック
------------
追加されたのは race_date が 2002-2006 の races/race_horses/race_results と、
新規 master 行だけ。件数を JSON に記録する。

    cd ingest && uv run python ../scripts/canary_ingest_2002_2006.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import time
from pathlib import Path

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import (
    Horse,
    IngestionJob,
    Jockey,
    Race,
    RaceHorse,
    RaceResult,
    Trainer,
)
from horseracing_db.session import create_db_engine
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from horseracing_ingest.mapping import MappingError, to_core_records
from horseracing_ingest.parser import ParsedRow, RowError, parse_rows

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
MASTER = {Horse: ("horse_id",), Jockey: ("jockey_id",), Trainer: ("trainer_id",)}


def _upsert(session, model, values: dict, pk: tuple[str, ...], *, master: bool) -> None:
    stmt = insert(model).values(**values)
    if master:
        # 後方 backfill: 既存マスタは絶対に触らない
        session.execute(stmt.on_conflict_do_nothing(index_elements=list(pk)))
        return
    upd = {c: getattr(stmt.excluded, c) for c in values if c not in pk}
    session.execute(stmt.on_conflict_do_update(index_elements=list(pk), set_=upd)
                    if upd else stmt.on_conflict_do_nothing(index_elements=list(pk)))


def upsert_backfill(session, rec) -> None:
    _upsert(session, Race, rec.race, ("race_id",), master=False)
    _upsert(session, Horse, rec.horse, ("horse_id",), master=True)
    if rec.jockey:
        _upsert(session, Jockey, rec.jockey, ("jockey_id",), master=True)
    if rec.trainer:
        _upsert(session, Trainer, rec.trainer, ("trainer_id",), master=True)
    _upsert(session, RaceHorse, rec.race_horse, ("race_id", "horse_id"), master=False)
    if rec.race_result is not None:
        _upsert(session, RaceResult, rec.race_result, ("race_id", "horse_id"), master=False)


def master_checksum(session) -> tuple[str, int]:
    """既存 horses 行の内容 checksum(取込の前後で不変であるべき)。"""
    rows = session.execute(text(
        "select horse_id, coalesce(horse_name,''), coalesce(sex,''), "
        "coalesce(birth_year::text,''), coalesce(sire_name,''), coalesce(dam_name,''), "
        "coalesce(damsire_name,''), coalesce(owner_name,''), coalesce(breeder_name,''), "
        "coalesce(sire_line,''), coalesce(damsire_line,'') "
        "from horses order by horse_id")).fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update("\x1f".join(r).encode())
        h.update(b"\x1e")
    return h.hexdigest(), len(rows)


def counts(session) -> dict:
    q = lambda s: int(session.execute(text(s)).scalar_one())  # noqa: E731
    return {
        "races": q("select count(*) from races"),
        "race_horses": q("select count(*) from race_horses"),
        "race_results": q("select count(*) from race_results"),
        "horses": q("select count(*) from horses"),
        "jockeys": q("select count(*) from jockeys"),
        "trainers": q("select count(*) from trainers"),
        "races_2002_2006": q("select count(*) from races where race_date "
                             "between '2002-01-01' and '2006-12-31'"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-year", type=int, default=2002)
    ap.add_argument("--to-year", type=int, default=2006)
    ap.add_argument("--raw-dir", default="../raw_data/jra-van")
    ap.add_argument("--dry-run", action="store_true", help="1 年だけ 2000 行で試す")
    ap.add_argument("--json", dest="json_out", default="../out/canary_ingest.json")
    args = ap.parse_args()

    engine = create_db_engine(DB)
    # **この run 自身の開始時刻**を保持する。ingestion_jobs から引くと、過去の backfill run の
    # ジョブまで拾ってしまい照合対象がずれる(実際に 2 度誤検知した)。
    run_started = datetime.datetime.now(datetime.UTC)
    with Session(engine) as session:
        before_hash, before_n = master_checksum(session)
        before = counts(session)
    print(f"取込前: horses={before['horses']:,} races={before['races']:,} "
          f"(2002-2006 は {before['races_2002_2006']:,})")
    print(f"  既存 horses checksum = {before_hash[:16]}… ({before_n:,} 行)\n")

    years = range(args.from_year, args.to_year + 1)
    per_year = []
    for y in years:
        path = Path(args.raw_dir) / str(y)
        if not path.exists():
            raise SystemExit(f"{path} が無い")
        t0, n_row, n_err, rids = time.time(), 0, 0, set()
        with Session(engine) as session:
            job = IngestionJob(source=Source.JRA_VAN, job_type="historical_year_backfill",
                               scope="year", scope_value=str(y), status=JobStatus.RUNNING,
                               started_at=datetime.datetime.now(datetime.UTC))
            session.add(job)
            session.flush()
            for item in parse_rows(path):
                if isinstance(item, RowError):
                    n_err += 1
                    continue
                if not isinstance(item, ParsedRow):
                    continue
                try:
                    rec = to_core_records(item)
                except MappingError:
                    n_err += 1
                    continue
                upsert_backfill(session, rec)
                rids.add(rec.race_id)
                n_row += 1
                if n_row % 1000 == 0:
                    session.flush()
                if args.dry_run and n_row >= 2000:
                    break
            job.status = JobStatus.PARTIAL if n_err else JobStatus.SUCCEEDED
            job.processed_rows, job.error_count = n_row, n_err
            job.completed_at = datetime.datetime.now(datetime.UTC)
            job.summary = {"races": len(rids), "race_horses": n_row,
                           "backfill_safe_master": True}
            if args.dry_run:
                session.rollback()
                print(f"  {y} dry-run: {n_row:,} 行 / {len(rids):,} レース "
                      f"({time.time()-t0:.1f}s) — ロールバック")
                break
            session.commit()
        per_year.append({"year": y, "rows": n_row, "races": len(rids), "errors": n_err,
                         "seconds": round(time.time() - t0, 1)})
        print(f"  {y}: {n_row:,} 行 / {len(rids):,} レース / エラー {n_err} "
              f"({time.time()-t0:.1f}s)")

    with Session(engine) as session:
        after_hash, after_n = master_checksum(session)
        after = counts(session)
    print("\n=== 検証 ===")
    new_ids = after["horses"] - before["horses"]
    print(f"  horses {before['horses']:,} → {after['horses']:,} (+{new_ids:,})")
    print(f"  races  {before['races']:,} → {after['races']:,} "
          f"(2002-2006: {after['races_2002_2006']:,})")
    if args.dry_run:
        ok = after_hash == before_hash and after == before
        print(f"  dry-run: 何も変わっていない = {ok}")
    else:
        # 既存行だけの checksum を取り直して比較する
        with Session(engine) as session:
            rows = session.execute(text(
                "select horse_id, coalesce(horse_name,''), coalesce(sex,''), "
                "coalesce(birth_year::text,''), coalesce(sire_name,''), coalesce(dam_name,''), "
                "coalesce(damsire_name,''), coalesce(owner_name,''), coalesce(breeder_name,''), "
                "coalesce(sire_line,''), coalesce(damsire_line,'') "
                "from horses where created_at < :t order by horse_id"),
                {"t": run_started}).fetchall()
        h = hashlib.sha256()
        for r in rows:
            h.update("\x1f".join(r).encode())
            h.update(b"\x1e")
        same = (h.hexdigest() == before_hash and len(rows) == before_n)
        print(f"  既存 horses 行の checksum 不変 = {same}  ({len(rows):,} 行を照合)")
        if not same:
            raise SystemExit("!! 既存マスタが書き換わった(fail-closed)")

    with open(args.json_out, "w") as fh:
        json.dump({"dry_run": args.dry_run, "years": per_year,
                   "before": before, "after": after,
                   "master_checksum_before": before_hash,
                   "master_checksum_after": after_hash,
                   "rollback": "delete from race_results/race_horses/races where race_date "
                               "between 2002-01-01 and 2006-12-31; 新規 master 行は "
                               "created_at で識別"}, fh, indent=2)
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
