"""Feature 088 T013: coverage audit for the finish_decomp bundle (FR-018 / SC-006).

Reports, from a materialized features-020 parquet:
  1. per-column non-missing rate by YEAR (the 10 bundle columns + the existing avg_last3_finish /
     prev_finish as the comparison baseline — the min_periods asymmetry is expected, see
     data-model.md),
  2. the count of out-of-range finish_order rows (INV-C2a data errors) and single-starter races
     (INV-C2 degenerate denominator) measured straight from the DB,
  3. overall bundle non-missing rates.

Usage:
    DATABASE_URL=... uv run --project features python scripts/finish_decomp_coverage.py \
        artifacts/features_020.parquet
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

_BUNDLE = [
    "prev_finish_pct", "prev2_finish", "prev3_finish", "prev2_finish_pct", "prev3_finish_pct",
    "avg_last3_finish_pct", "avg_last5_finish", "avg_last5_finish_pct", "best_finish_pct",
    "finish_trend5",
]
_BASELINE = ["prev_finish", "avg_last3_finish"]


def _db_anomalies() -> None:
    """Range violations and degenerate denominators, straight from the DB (FR-018)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("\n[db] DATABASE_URL unset — skipping range/degenerate audit")
        return
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    with eng.connect() as c:
        row = c.execute(text("""
            WITH sizes AS (
                SELECT race_id, count(*) AS n_started
                FROM race_horses WHERE entry_status = 'started' GROUP BY race_id
            )
            SELECT
              (SELECT count(*) FROM sizes WHERE n_started = 1)                      AS single_starter_races,
              (SELECT count(*) FROM race_results r JOIN sizes s USING (race_id)
                 WHERE r.result_status = 'finished'
                   AND (r.finish_order < 1 OR r.finish_order > s.n_started))        AS out_of_range_runs,
              (SELECT count(*) FROM race_results
                 WHERE result_status = 'finished')                                  AS finished_runs
        """)).fetchone()
    print("\n[db] INV-C2 / INV-C2a audit")
    print(f"  finished runs             : {row[2]:,}")
    print(f"  out-of-range finish_order : {row[1]:,}  ({row[1] / max(row[2], 1):.6%} of finished)")
    print(f"  single-starter races      : {row[0]:,}")


def main(parquet: str) -> int:
    df = pd.read_parquet(Path(parquet))
    missing = [c for c in _BUNDLE if c not in df.columns]
    if missing:
        print(f"FAIL: parquet lacks bundle columns {missing} (is this a features-020 build?)")
        return 1

    year = pd.to_datetime(df["race_id"].str[:4], format="%Y").dt.year
    cols = _BUNDLE + [c for c in _BASELINE if c in df.columns]

    print(f"rows={len(df):,}  ({parquet})")
    print("\n[overall] non-missing rate")
    for c in cols:
        tag = " (baseline)" if c in _BASELINE else ""
        print(f"  {c:<24} {df[c].notna().mean():7.3%}{tag}")

    print("\n[by year] non-missing rate")
    by_year = df.assign(_y=year).groupby("_y")[cols].apply(lambda g: g.notna().mean())
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print((by_year * 100).round(1).to_string())

    _db_anomalies()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
