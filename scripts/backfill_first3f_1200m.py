"""1200m のテン3F を既存行に埋める(取得ゼロ・純粋な導出)。

`race_results.first_3f` は JRA-VAN 生 CSV の col55 由来で、供給停止により 2026 年は 0.0% に
なった。netkeiba は馬ごとの上がり3F しか出さず、テン3F は出さない — ラップページが持つのは
レース単位の先頭ペースであって馬ごとの値ではないので、ラップを取っても届かない。

唯一の経路が 1200m の恒等式である。JRA-VAN の col55 と `finish_time - last_3f` は 1200m のとき
**187,833 行で平均誤差 0.0000 秒**、他距離では 3〜50 秒ずれて全く一致しない。つまり JRA 自身が
その定義で出しており、ここでの計算は推定ではなく同じ定義の再現。

`--apply` を付けるまで書き込まない。既存値は上書きしない(JRA-VAN の実測を守る)。
"""

from __future__ import annotations

import argparse
import os

from horseracing_db.session import create_db_engine
from sqlalchemy import text

SQL_PREVIEW = text("""
SELECT count(*) AS n,
       min(r.race_date) AS from_date, max(r.race_date) AS to_date,
       round(avg(extract(epoch FROM rr.finish_time) - rr.last_3f)::numeric, 3) AS mean_sec
FROM race_results rr JOIN races r ON r.race_id = rr.race_id
WHERE r.distance = 1200 AND rr.first_3f IS NULL
  AND rr.finish_time IS NOT NULL AND rr.last_3f IS NOT NULL
  AND extract(epoch FROM rr.finish_time) - rr.last_3f > 0
""")

SQL_APPLY = text("""
UPDATE race_results rr
SET first_3f = (extract(epoch FROM rr.finish_time) - rr.last_3f)::numeric
FROM races r
WHERE r.race_id = rr.race_id
  AND r.distance = 1200 AND rr.first_3f IS NULL
  AND rr.finish_time IS NOT NULL AND rr.last_3f IS NOT NULL
  AND extract(epoch FROM rr.finish_time) - rr.last_3f > 0
""")

#: 既に値がある 1200m 行で恒等式を検算する。ここがずれるなら前提が崩れているので書き込まない。
SQL_VERIFY = text("""
SELECT count(*) AS n,
       round(max(abs(extract(epoch FROM rr.finish_time) - rr.last_3f - rr.first_3f))::numeric, 4)
         AS worst_sec
FROM race_results rr JOIN races r ON r.race_id = rr.race_id
WHERE r.distance = 1200 AND rr.first_3f IS NOT NULL
  AND rr.finish_time IS NOT NULL AND rr.last_3f IS NOT NULL
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実際に書き込む(既定は dry-run)")
    ap.add_argument("--max-error-sec", type=float, default=0.01,
                    help="検算の許容誤差。超えたら中断する")
    args = ap.parse_args()

    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    engine = create_db_engine()
    with engine.begin() as c:
        v = c.execute(SQL_VERIFY).one()
        print(f"検算: 既存値のある 1200m {v.n:,} 行で恒等式の最大誤差 = {v.worst_sec} 秒")
        if v.n == 0:
            print("  検算対象が無い。前提を確認できないので中断する。")
            return 1
        if float(v.worst_sec) > args.max_error_sec:
            print(f"  許容 {args.max_error_sec} 秒を超えた。前提が崩れているので書き込まない。")
            return 1

        p = c.execute(SQL_PREVIEW).one()
        print(f"対象: {p.n:,} 行  ({p.from_date} 〜 {p.to_date})  平均テン3F={p.mean_sec} 秒")
        if not args.apply:
            print("\n  dry-run。書き込むには --apply。")
            return 0
        n = c.execute(SQL_APPLY).rowcount
        print(f"\n  {n:,} 行を更新した。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
