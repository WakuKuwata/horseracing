"""稼働モデルが依存している特徴のうち、現行レジームで死んでいる/薄いものを出す。

このプロジェクトで最近効いた改善はすべてこの系統だった — 091 体重マスク -0.0106、本賞金 backfill
-0.0098、重賞ラベル修正 -0.0129。いずれも**新しい情報をゼロ**で、既にある情報がモデルに届いて
いなかっただけである。新規特徴の screening は生存ゼロ(feature-axis-audit-2026-08)なので、探す
べきはこちら。

出力は「モデルの split 重要度」×「2026 の充足率が学習窓に対してどれだけ落ちたか」で並べる。
重要度が低い列が枯れていても実害は小さく、重要度が高い列が枯れていれば静かに壊れている。

**モデル入力を 1 列残らず測る**。materialize 対象は parquet(全期間・全行)から、対象外の静的列
(race_class / prize_rel / sire_line / going / weight …)は各窓のレースを標本抽出して投影ビルド
した値から測る。2026-08-25 まではこの静的列 25 本が丸ごと監査から外れており、出力では
「parquet に無い=要調査」と誤警告されるだけだった — sire_line が 2026 に 10pt 落ちていたことは
手書き SQL を足して初めて見えた。

静的列を「供給元テーブルの NULL 率」でなく **ビルド後の値**で測るのは意図的である。供給元が
100% でも導出が壊れれば入力は NaN になる(class_rank のマッピング不一致で class_transition が
55.7% NaN だった実例がある)。供給元カバレッジではその型の欠陥を一件も検出できない。

実行(lightgbm と特徴層の両方が要るので training の env で回す):

    uv run --project training python scripts/feature_decay_audit.py

parquet が現行 DB より古いと materialize 側の数字だけが古い断面になる。供給元切替を追うのが
目的なので、先に `features materialize` を回してから使うこと。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from horseracing_db.session import create_db_engine
from horseracing_features.builder import build_feature_matrix
from sqlalchemy import text
from sqlalchemy.orm import Session

DB = os.environ.get("DATABASE_URL",
                    "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")


def _sample_races(session: Session, start: str, end: str, n: int) -> tuple[list[str], str]:
    """Evenly-spaced races across a window, plus that window's last date.

    Evenly spaced rather than the first n: a窓 head sample would be one January, and several of the
    columns measured here are seasonal (going/weather) or drift within a year (supply cutovers land
    mid-year — the 2026 one did). Deterministic, so two runs of the audit are comparable.
    """
    rows = list(session.execute(text(
        "SELECT r.race_id, r.race_date FROM races r WHERE r.race_date BETWEEN :s AND :e "
        "AND EXISTS (SELECT 1 FROM race_horses rh WHERE rh.race_id = r.race_id) "
        "ORDER BY r.race_date, r.race_id"), {"s": start, "e": end}))
    if not rows:
        return [], end
    stride = max(1, len(rows) // n)
    picked = rows[::stride][:n]
    return [r[0] for r in picked], str(rows[-1][1])


def _built_coverage(session: Session, cols: list[str], start: str, end: str,
                    n: int) -> tuple[pd.Series, int, int]:
    """Non-missing rate of MODEL INPUT values (post-derivation) over a sample of a window."""
    race_ids, last_date = _sample_races(session, start, end, n)
    if not race_ids:
        return pd.Series(dtype=float), 0, 0
    df = build_feature_matrix(
        session,
        end_date=datetime.date.fromisoformat(last_date),
        wanted=frozenset(cols),
        target_race_ids=frozenset(race_ids),
    )
    have = [c for c in cols if c in df.columns]
    return df[have].notna().mean(), len(race_ids), len(df)


def _report_non_materialized(cols: list[str], imp_share: pd.Series, tt_year: int,
                             now_year: int, sample: int, top: int) -> None:
    """materialize 対象外(= parquet に無い)モデル入力を、投影ビルドの実値で測って同じ形で出す。"""
    print(f"\n=== materialize 対象外の入力({len(cols)} 列)を投影ビルドで測る ===")
    with Session(create_db_engine(DB)) as s:
        cov_train, n_r_tr, n_rows_tr = _built_coverage(
            s, cols, "2007-01-01", f"{tt_year}-12-31", sample)
        cov_now, n_r_now, n_rows_now = _built_coverage(
            s, cols, f"{now_year}-01-01", f"{now_year}-12-31", sample)
    if cov_train.empty or cov_now.empty:
        print("  標本が取れなかった(窓にレースが無い)")
        return
    print(f"  標本: 学習窓 {n_r_tr} レース/{n_rows_tr} 行 ・ {now_year} {n_r_now} レース/{n_rows_now} 行")
    out = pd.DataFrame({
        "split_share": imp_share.reindex(cov_now.index),
        f"充足_学習窓(~{tt_year})": cov_train,
        f"充足_{now_year}": cov_now,
        "低下": cov_train - cov_now,
    })
    out["影響"] = out["split_share"] * out["低下"].clip(lower=0)
    out = out.sort_values("影響", ascending=False)
    hit = out[out["影響"] > 0]
    print(hit.head(top).to_string(float_format=lambda v: f"{v:8.4f}") if not hit.empty
          else "  低下している列は無い")
    never = [c for c in cols if c not in cov_now.index]
    if never:
        # parquet にも投影ビルドにも現れない列だけが本物の「要調査」。モデルが要求する名前が
        # 現行の特徴層のどこからも出てこない = 版ずれか列の消滅。
        print(f"\n  !! parquet にも投影ビルドにも無い入力(要調査): {never}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="artifacts/features.parquet")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--sample", type=int, default=600,
                    help="非 materialize 列を測るために各窓から抜くレース数(投影ビルド)")
    args = ap.parse_args()

    eng = create_db_engine(DB)
    with eng.connect() as c:
        mv, uri = c.execute(text(
            "select model_version, weights_uri from model_versions where adoption_status='active'"
        )).one()
    print(f"active = {mv}")
    booster = lgb.Booster(model_file=uri)
    meta = json.loads((Path(uri).parent / "metadata.json").read_text())
    train_through = str(meta.get("training", {}).get("train_through") or meta.get("train_through"))
    print(f"booster の学習終端 = {train_through}")

    names = booster.feature_name()
    split = booster.feature_importance(importance_type="split")
    imp = pd.Series(split, index=names).sort_values(ascending=False)
    imp_share = imp / imp.sum()

    df = pd.read_parquet(args.parquet)
    # parquet は race_id しか持たない。JRA-VAN の 12 桁 id は先頭 4 桁が年。
    df["year"] = df["race_id"].astype(str).str[:4].astype(int)
    cols = [c for c in names if c in df.columns]
    missing_cols = [c for c in names if c not in df.columns]

    # 学習窓(booster が実際に見た期間)と現行レジームで、非欠損率を比べる
    # arm E 以降 booster の学習終端は現在に届くが、現行レジームの行は窓の末端にわずかしか無い。
    # 「モデルが厚く学んだ期間」と「いま予測している期間」を比べたいので、末端 2 年を除いて基準にする。
    tt_year = min(int(train_through[:4]) if train_through[:4].isdigit() else 2024,
                  int(df["year"].max()) - 2)
    train_mask = df["year"] <= tt_year
    now_mask = df["year"] == df["year"].max()
    cov_train = df.loc[train_mask, cols].notna().mean()
    cov_now = df.loc[now_mask, cols].notna().mean()
    drop = cov_train - cov_now

    out = pd.DataFrame({
        "split_share": imp_share.reindex(cols),
        f"充足_学習窓(~{tt_year})": cov_train,
        f"充足_{int(df['year'].max())}": cov_now,
        "低下": drop,
    })
    out["影響"] = out["split_share"] * out["低下"].clip(lower=0)
    out = out.sort_values("影響", ascending=False)

    print(f"\n=== 重要度×低下 の上位 {args.top} ===")
    show = out[out["影響"] > 0].head(args.top)
    if show.empty:
        print("  低下している列は無い(重要度を持つ列の充足率は現行レジームでも維持されている)")
    else:
        print(show.to_string(float_format=lambda v: f"{v:8.4f}"))

    print("\n=== 参考: 現行レジームで充足率 <50% の列(重要度順) ===")
    thin = out[out[f"充足_{int(df['year'].max())}"] < 0.5].sort_values("split_share", ascending=False)
    print(thin.head(15).to_string(float_format=lambda v: f"{v:8.4f}") if not thin.empty else "  なし")

    if missing_cols:
        _report_non_materialized(missing_cols, imp_share, tt_year, int(df["year"].max()),
                                 args.sample, args.top)


if __name__ == "__main__":
    main()
