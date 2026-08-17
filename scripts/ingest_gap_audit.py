"""日次取込の取りこぼし監査(読み取り専用・ネットワーク非使用)。

なぜ要るか: 開催日の取込を朝に 1 回だけ叩くと、結果はまだ存在しないので `results` ジョブが
失敗し、そのまま欠けたままになる(2026-08-16 の実例。朝 06:12 の 1 回のみで夜の回が無く、
翌日まで結果ゼロだった)。**結果は消えないので遅れても取り返せるが、気づかなければ永久に
欠ける。** 前向き holdout は開催日数で進むため、1 日落とすたびに判定日が後ろへずれる。

このスクリプトは「何が欠けているか」と「それを埋めるコマンド」を出すだけで、取得はしない。
netkeiba への request は 1 本も出さない([[netkeiba-scraping-budget]])。

使い方:
    cd training && uv run python ../scripts/ingest_gap_audit.py
    cd training && uv run python ../scripts/ingest_gap_audit.py --from 2026-01-01

終了コード: 埋められる欠損があれば 1、無ければ 0(定期実行に載せられる)。
"""

from __future__ import annotations

import argparse
import datetime
import sys

from sqlalchemy import create_engine, text

DEFAULT_DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
OPS_URL = "http://127.0.0.1:8001/ops/v1/days/{date}/refresh"

#: 085 の事前登録: prospective holdout は 2026-07-12 より後の未使用レースで判定する。
#: 必要開催日数は §7 の外挿値(効果 -0.012 に対し約 27 開催日)。
PROSPECTIVE_FROM = datetime.date(2026, 7, 12)
PROSPECTIVE_TARGET_DAYS = 27


def _is_new_year_break(d: datetime.date) -> bool:
    """JRA の年末年始休み。曜日ヒューリスティックの誤検知を潰す。

    2007-2026 の実データで 12/29〜1/3 の開催は **0 レース**、開催初日は毎年 1/4〜1/6。
    休み明けの初週末(1/4〜1/6)は年により開催したりしなかったりする(2025 は 1/5 開始なので
    1/4 土は開催なし)ので、そこまで除外する。代償はその 3 日間の「丸ごと未取込」を曜日では
    検知できないこと — ただし entries が入っていれば §2 の結果欠損側で拾える。
    """
    return (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 6)


def audit(conn, date_from: datetime.date, today: datetime.date) -> int:
    n_actionable = 0

    # --- 1. 前向き holdout の進捗 -------------------------------------------------------
    row = conn.execute(text("""
        select count(distinct r.race_date), count(distinct r.race_id)
        from races r join race_results rr on rr.race_id = r.race_id
        where r.race_date > :d"""), {"d": PROSPECTIVE_FROM}).one()
    days, races = row
    pct = 100.0 * days / PROSPECTIVE_TARGET_DAYS
    print(f"【前向き holdout(085 arm E)】{days} / 約{PROSPECTIVE_TARGET_DAYS} 開催日 "
          f"({pct:.0f}%) · {races} レース確定")
    if days < PROSPECTIVE_TARGET_DAYS:
        weeks = (PROSPECTIVE_TARGET_DAYS - days) / 2.0  # 週 2 開催日(土日)
        print(f"  残り 約{PROSPECTIVE_TARGET_DAYS - days} 開催日 ≒ {weeks:.0f} 週")

    # --- 2. 結果が欠けている過去の開催日(埋められる) -------------------------------------
    print(f"\n【結果の欠損】{date_from} 以降 · 本日({today})より前の開催日")
    rows = list(conn.execute(text("""
        select r.race_date,
               count(distinct r.race_id),
               count(distinct rr.race_id)
        from races r
        left join race_results rr on rr.race_id = r.race_id
        where r.race_date >= :a and r.race_date < :b
        group by r.race_date
        having count(distinct rr.race_id) < count(distinct r.race_id)
        order by r.race_date"""), {"a": date_from, "b": today}))
    if not rows:
        print("  なし")
    for d, n, res in rows:
        n_actionable += 1
        print(f"  {d}  races={n:>3} results={res:>3}  ({n - res} 欠)")
        print(f"      curl -s -X POST {OPS_URL.format(date=d)}")

    # --- 3. 丸ごと未取込の疑い(ヒューリスティック) ---------------------------------------
    # 2026 の実測では土日 65 日すべてに開催があり、抜けはゼロだった。したがって「開催の無い
    # 土日」は日次取込が丸ごと走らなかった強い兆候になる。ただし平日開催(祝日, 2026 は 3 日)
    # は曜日では検知できない — この経路の欠損はここには出ない。
    have = {r[0] for r in conn.execute(text(
        "select distinct race_date from races where race_date >= :a and race_date < :b"),
        {"a": date_from, "b": today})}
    print("\n【未取込の疑い】開催の無い土日(年末年始・平日開催は検知不能)")
    suspects = []
    d = date_from
    while d < today:
        if d.weekday() >= 5 and d not in have and not _is_new_year_break(d):
            suspects.append(d)
        d += datetime.timedelta(days=1)
    if not suspects:
        print("  なし")
    for d in suspects:
        n_actionable += 1
        print(f"  {d} ({'土日'[d.weekday() - 5]})  races=0")
        print(f"      curl -s -X POST {OPS_URL.format(date=d)}")

    # --- 4. オッズの欠損(参考・埋められない) ---------------------------------------------
    # 単勝オッズは発走後に取得できないので、過去日の欠損は恒久。埋める手段が無いため
    # actionable には数えないが、荒れ度・市場特徴の被覆に効くので見えるようにしておく。
    print(f"\n【オッズ欠損(参考・回復不能)】{date_from} 以降")
    rows = list(conn.execute(text("""
        select r.race_date, count(distinct r.race_id),
               count(distinct rh.race_id) filter (where rh.odds is not null)
        from races r
        left join race_horses rh on rh.race_id = r.race_id
        where r.race_date >= :a and r.race_date < :b
        group by r.race_date
        having count(distinct rh.race_id) filter (where rh.odds is not null)
             < count(distinct r.race_id)
        order by r.race_date"""), {"a": date_from, "b": today}))
    if not rows:
        print("  なし")
    for d, n, od in rows:
        print(f"  {d}  races={n:>3} odds={od:>3}  ({n - od} 欠)")

    print()
    if n_actionable:
        print(f"→ 埋められる欠損 {n_actionable} 件。上の curl を順に叩く"
              f"(冪等・何度でも安全)。")
    else:
        print("→ 埋められる欠損なし。")
    return n_actionable


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="date_from", type=datetime.date.fromisoformat, default=None,
                   help="監査の開始日(既定: 60 日前)")
    p.add_argument("--today", type=datetime.date.fromisoformat, default=None,
                   help="基準日(既定: 実行日)。当日は発走前があるので監査対象に含めない")
    p.add_argument("--database-url", default=DEFAULT_DB)
    args = p.parse_args()

    today = args.today or datetime.date.today()
    date_from = args.date_from or (today - datetime.timedelta(days=60))

    engine = create_engine(args.database_url)
    with engine.connect() as conn:
        return 1 if audit(conn, date_from, today) else 0


if __name__ == "__main__":
    sys.exit(main())
