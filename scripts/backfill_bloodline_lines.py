"""Fill `horses.sire_line` / `damsire_line` for netkeiba horses from the names we already hold.

The two LINE columns are categorical model inputs (056 sire_line group) that only ever came from
the JRA-VAN CSV, so every `nk:` horse — 39% of 2026 started rows — has them NULL. The line is a
pure function of the sire's NAME in the existing data (measured 2026-08-22: 1,679 sires, none
with two distinct lines; 3,080 / 4,196 nk: horses resolvable, 0 ambiguous), so this is a local
join: **zero netkeiba requests**, idempotent (fills NULL only), dry-run first.

Ingest now derives the same thing for new horses (scrape/upsert.py complete_horse_profile), so
this is a one-off catch-up. After running it re-materialise the feature parquet.

    DATABASE_URL=... uv run --project scrape python scripts/backfill_bloodline_lines.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys

from horseracing_db.session import create_db_engine
from sqlalchemy import text
from sqlalchemy.orm import Session

# mode() picks the single line; HAVING count(distinct)=1 refuses ambiguous names (never guess).
_LINES_SQL = """
WITH lines AS (
  SELECT {name_col} AS name, min({line_col}) AS line
  FROM horses WHERE {line_col} IS NOT NULL AND {name_col} IS NOT NULL
  GROUP BY {name_col} HAVING count(DISTINCT {line_col}) = 1
)
SELECT h.horse_id, h.{name_col} AS name, l.line
FROM horses h LEFT JOIN lines l ON l.name = h.{name_col}
WHERE h.{line_col} IS NULL AND h.{name_col} IS NOT NULL
ORDER BY h.horse_id
"""
_UPDATE_SQL = "UPDATE horses SET {line_col} = :line WHERE horse_id = :hid AND {line_col} IS NULL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")

    rc = 0
    with Session(create_db_engine()) as s:
        for line_col, name_col in (("sire_line", "sire_name"), ("damsire_line", "damsire_name")):
            rows = list(s.execute(text(_LINES_SQL.format(line_col=line_col, name_col=name_col))))
            resolvable = [r for r in rows if r.line is not None]
            unresolved = len(rows) - len(resolvable)
            print(f"{line_col}: {len(rows)} NULL rows with a {name_col}; "
                  f"{len(resolvable)} resolvable from existing horses, {unresolved} unresolved "
                  f"(name unknown or ambiguous -> left NULL)")
            if args.dry_run:
                for r in resolvable[:5]:
                    print(f"    {r.horse_id}  {r.name!r} -> {r.line!r}")
                continue
            # one row per transaction: the profile-completion job may be updating the same
            # horses rows concurrently (it commits per batch), and a single multi-thousand-row
            # transaction here would either block behind it or deadlock it. Per-row commits hold
            # at most one row lock for a moment and can never deadlock a batch.
            for r in resolvable:
                s.execute(text(_UPDATE_SQL.format(line_col=line_col)),
                          {"line": r.line, "hid": r.horse_id})
                s.commit()
            recheck = s.execute(text(_LINES_SQL.format(line_col=line_col, name_col=name_col)))
            left = sum(1 for r in recheck if r.line is not None)
            print(f"  filled {len(resolvable)}; re-check resolvable-but-NULL = {left} (expected 0)")
            rc |= int(left != 0)
        if args.dry_run:
            print("\ndry run: nothing written")
    return rc


if __name__ == "__main__":
    sys.exit(main())
