"""Stage 1 of the body-weight kill-test: build + cache the evaluation inputs.

Caches to scratchpad:
  feats.parquet   - feature matrix rows for eval races (started horses)
  aux.parquet     - race_date, winner flag, prev_weight (strictly-before carry-forward)
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine
from horseracing_features.builder import build_feature_matrix

SCRATCH = Path(__file__).parent
END_DATE = dt.date(2026, 7, 12)      # materialized parquet data_through
FROM_DATE = dt.date(2021, 1, 1)      # booster OOS (booster fit stops 2020-08-29)
MATERIALIZED = Path("/Users/kuwatawaku/workspace/horseracing/artifacts/features.parquet")

# strictly-before carry-forward of the last non-null body weight, per horse.
# grp counts non-null weights seen so far -> first_value within (horse, grp) is the last
# non-null value; lag() by one entry makes it strictly previous. Same-day previous entries
# are nulled out explicitly (repo convention: same-day exclusion).
PREV_WEIGHT_SQL = text("""
WITH base AS (
  SELECT rh.race_id, rh.horse_id, r.race_date, rh.weight,
         count(rh.weight) OVER (
           PARTITION BY rh.horse_id ORDER BY r.race_date, rh.race_id
           ROWS UNBOUNDED PRECEDING
         ) AS grp
  FROM race_horses rh JOIN races r ON r.race_id = rh.race_id
),
ff AS (
  SELECT race_id, horse_id, race_date, grp,
         first_value(weight) OVER (
           PARTITION BY horse_id, grp ORDER BY race_date, race_id
         ) AS w_ff
  FROM base
)
SELECT race_id, horse_id,
       lag(w_ff)      OVER w AS prev_weight,
       lag(race_date) OVER w AS prev_weight_date
FROM ff
WINDOW w AS (PARTITION BY horse_id ORDER BY race_date, race_id)
""")

WINNER_SQL = text("""
SELECT rr.race_id, rr.horse_id, rr.finish_order, rr.result_status
FROM race_results rr
JOIN races r ON r.race_id = rr.race_id
WHERE r.race_date BETWEEN :d0 AND :d1 AND rr.finish_order = 1
""")

RACEMETA_SQL = text("""
SELECT r.race_id, r.race_date
FROM races r
WHERE r.race_date BETWEEN :d0 AND :d1
  AND EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id = r.race_id)
""")


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    engine = create_db_engine()
    t0 = time.time()
    with Session(engine) as session:
        cache_all = SCRATCH / "feats_all.parquet"
        if cache_all.exists():
            print("[1/4] reusing cached full feature matrix ...", flush=True)
            feats = pd.read_parquet(cache_all)
            print(f"    cached: {feats.shape} in {time.time()-t0:.1f}s", flush=True)
        else:
            print("[1/4] building feature matrix (materialized) ...", flush=True)
            try:
                feats = build_feature_matrix(
                    session, end_date=END_DATE,
                    materialized_path=MATERIALIZED, use_materialized=True,
                )
                src = "materialized+verified"
            except Exception as exc:  # fail-closed fingerprint etc.
                print(f"    materialized path failed: {type(exc).__name__}: {exc}", flush=True)
                print("    falling back to full in-memory build ...", flush=True)
                feats = build_feature_matrix(session, end_date=END_DATE)
                src = "in-memory"
            print(f"    {src}: {feats.shape} in {time.time()-t0:.1f}s", flush=True)
            feats.to_parquet(cache_all, index=False)

        print("[2/4] race metadata ...", flush=True)
        meta = pd.read_sql(RACEMETA_SQL, session.connection(),
                           params={"d0": FROM_DATE, "d1": END_DATE})
        print(f"    settled races in window: {len(meta)}", flush=True)

        print("[3/4] winners ...", flush=True)
        win = pd.read_sql(WINNER_SQL, session.connection(),
                          params={"d0": FROM_DATE, "d1": END_DATE})
        print(f"    finish_order=1 rows: {len(win)}", flush=True)

        print("[4/4] prev weight carry-forward ...", flush=True)
        prev = pd.read_sql(PREV_WEIGHT_SQL, session.connection())
        print(f"    prev-weight rows: {len(prev)}", flush=True)

    keep = set(meta["race_id"])
    feats = feats[feats["race_id"].isin(keep)].copy()
    print(f"eval feature rows (started, in window): {feats.shape}", flush=True)

    prev = prev[prev["race_id"].isin(keep)].copy()
    feats.to_parquet(SCRATCH / "feats.parquet", index=False)
    meta.to_parquet(SCRATCH / "meta.parquet", index=False)
    win.to_parquet(SCRATCH / "winners.parquet", index=False)
    prev.to_parquet(SCRATCH / "prev_weight.parquet", index=False)
    print(f"cached in {time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
