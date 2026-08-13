"""Repair `races.race_class` for graded races the netkeiba cutover flattened to `オープン`.

The parser now canonicalises this at ingest (scrape/parse/entries.py), but rows already in the
database were written before that. Nothing needs re-fetching: the grade is sitting in the `grade`
column of the very same row, so this is a pure local repair — **zero netkeiba requests**.

Measured cost of leaving it alone: winner NLL −0.0129 over the 484 races the defect reaches
(scripts/killtest_grade.py). See memory/grade-lost-at-cutover.md.

Safety:
  * only touches rows where `grade` says G1/G2/G3 AND `race_class` carries no grade of its own —
    a row that already looks like the JRA-VAN era is left completely alone
  * idempotent: running it twice is a no-op, because the second pass no longer matches
  * `--dry-run` first, and it prints what it would change
  * `grade` itself is never modified, so netkeiba's own value stays as provenance

After running it you MUST re-materialise the feature parquet — `race_class` is inside the
materialise fingerprint, so reads fail closed until you do (which is the intended behaviour, not
an error):

    uv run --project features python -m horseracing_features materialize --out artifacts/features.parquet
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine

#: JRA-VAN's own spelling. Full-width on purpose: `race_class` is a CATEGORICAL model input, and
#: fifteen years of training data contain `Ｇ１`, never `G1`.
CLASS_BY_GRADE = {"G1": "Ｇ１", "G2": "Ｇ２", "G3": "Ｇ３"}

SELECT_SQL = text("""
SELECT race_id, race_date, grade, race_class
FROM races
WHERE grade IN ('G1','G2','G3')
  AND race_class NOT LIKE '%Ｇ%' AND race_class NOT LIKE '%G%'
ORDER BY race_date, race_id
""")

UPDATE_SQL = text("UPDATE races SET race_class = :rc WHERE race_id = :rid")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="list the work, change nothing")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")

    with Session(create_db_engine()) as s:
        rows = list(s.execute(SELECT_SQL))
        if not rows:
            print("nothing to repair (already canonical, or no graded races from netkeiba)")
            return 0

        by_grade: dict[str, int] = {}
        for r in rows:
            by_grade[r.grade] = by_grade.get(r.grade, 0) + 1
        span = f"{rows[0].race_date} .. {rows[-1].race_date}"
        print(f"{len(rows)} races to repair ({span})")
        for g in sorted(by_grade):
            print(f"  {g} -> {CLASS_BY_GRADE[g]}  ({by_grade[g]} races, "
                  f"currently '{next(r.race_class for r in rows if r.grade == g)}')")

        if args.dry_run:
            for r in rows[:10]:
                print(f"    {r.race_date} {r.race_id}  {r.race_class!r} -> "
                      f"{CLASS_BY_GRADE[r.grade]!r}")
            if len(rows) > 10:
                print(f"    ... and {len(rows) - 10} more")
            print("\ndry run: nothing written")
            return 0

        for r in rows:
            s.execute(UPDATE_SQL, {"rc": CLASS_BY_GRADE[r.grade], "rid": r.race_id})
        s.commit()
        print(f"repaired {len(rows)} races")

        left = len(list(s.execute(SELECT_SQL)))
        print(f"re-check: {left} rows still match the defect (expected 0 — the run is idempotent)")
        print("\nNEXT: re-materialise the feature parquet, or materialised reads will fail closed:")
        print("  uv run --project features python -m horseracing_features materialize "
              "--out artifacts/features.parquet")
        return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
