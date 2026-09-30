"""Parity: product feature builder (horseracing_training.market_ev) == research builder (rows.parquet).

Compares every shared column used by the market-aware model (and the market/history columns in general)
for all rows present in both, with exact equality (NaN == NaN). Writes evidence JSON.

    cd training && uv run python ../scripts/roi_explore/parity_market_ev.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from horseracing_training import market_ev

REPO = pathlib.Path(__file__).resolve().parents[2]
DB_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
COLS = [
    "odds", "q", "odds_rank", "fav_odds", "second_odds", "odds_gap12", "fav_q", "q_share_of_fav", "q_entropy_norm",
    "n_fav_under_2", "n_odds_under_10", "field_size", "is_last_race", "is_first_race", "race_ok",
    "career_starts", "career_wins", "career_win_rate", "career_top3_rate", "is_debut", "prev_finish", "prev2_finish",
    "prev3_finish", "avg_last3_finish", "best_finish_last5", "wins_last5", "top3_last5", "prev_popularity",
    "prev_odds", "prev_q", "prev_field_size", "prev_finish_pct", "prev_beat_market", "prev_distance",
    "prev_track_type", "prev_venue_code", "prev_class_canon", "dist_change", "class_change", "days_since_last",
    "tataki_2", "prev_weight", "weight_change_vs_prev", "prev_running_style", "prev_last3f_rank", "prev_margin_sec",
    "last_won", "jockey_change", "jockey_win_rate_365", "jockey_starts_365", "jockey_win_rate_all", "jockey_starts_all",
    "trainer_win_rate_365", "trainer_starts_365", "trainer_win_rate_all", "trainer_starts_all", "combo_starts_all",
    "combo_win_rate_all", "jockey_excess_365", "trainer_excess_365", "jockey_excess_all", "trainer_excess_all",
    "race_class_canon", "dist_band", "grade", "is_graded", "month", "dow", "year", "prize_money", "sire_line",
    "damsire_line", "venue_code", "track_type", "going", "weather", "sex", "age", "frame", "weight", "weight_diff",
    "jockey_weight", "distance", "race_number", "popularity",
]


def main() -> int:
    t0 = time.time()
    research = pd.read_parquet(REPO / "artifacts/roi_explore/rows.parquet")
    through = str(research["race_date"].max())
    with create_engine(DB_URL).connect() as c:
        raw = market_ev.load_rows(c, through=through, since="1986-01-01")
    prod = market_ev.build_features(raw)
    prod["race_date"] = prod["race_date"].dt.strftime("%Y-%m-%d")
    m = research.merge(prod, on=["race_id", "horse_id"], how="inner", suffixes=("_r", "_p"))
    report = {"rows_research": int(len(research)), "rows_product": int(len(prod)), "rows_joined": int(len(m)),
              "through": through, "columns": {}}
    bad = []
    for c in COLS:
        a, b = m[f"{c}_r"], m[f"{c}_p"]
        if pd.api.types.is_numeric_dtype(a) or pd.api.types.is_bool_dtype(a):
            x = a.astype(float).to_numpy(); y = b.astype(float).to_numpy()
            neq = ~((x == y) | (np.isnan(x) & np.isnan(y)))
        else:
            xa = a.astype(object).where(a.notna(), None).to_numpy(); yb = b.astype(object).where(b.notna(), None).to_numpy()
            neq = np.array([u != v for u, v in zip(xa, yb)])
        n = int(neq.sum())
        report["columns"][c] = n
        if n:
            bad.append((c, n))
    report["mismatched_columns"] = bad
    report["elapsed_s"] = round(time.time() - t0, 1)
    out = REPO / "artifacts/market_ev/parity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "columns"}, ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
