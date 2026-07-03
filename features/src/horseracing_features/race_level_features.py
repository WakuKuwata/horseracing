"""Feature 055: race-level (prize-money) features — a continuous class axis.

races.prize_money (1着賞金, 万円) is a PRE-PUBLISHED race condition (race-constant, verified
in research D4) — finer-grained than the categorical race_class. Two signals:

- asof_prize_avg  (as-of): the horse's past STARTED races' log1p(prize) expanding mean — its
  "prize class". Strictly-before via merge_asof(allow_exact_matches=False) (同日除外).
- prize_rel       (derived in the builder): today's log1p(prize) − asof_prize_avg = how far the
  horse moves up (+) / down (−) in class. Composed OUTSIDE this module because it mixes a
  current-race static with an as-of value (materialize stores only the as-of part).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames

#: the as-of column this module produces (materialized); prize_rel is builder-composed.
RACE_LEVEL_ASOF_COLUMNS = ["asof_prize_avg"]


def build_race_level_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id): asof_prize_avg over past started races (float64, NaN-propagating)."""
    races_cols = ["race_id", "race_date"]
    has_prize = "prize_money" in frames.races.columns
    if has_prize:
        races_cols.append("prize_money")
    races = frames.races[races_cols].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    if not has_prize:  # pre-055 fixtures -> all-NaN feature
        races["prize_money"] = np.nan

    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    runs = rh.merge(races, on="race_id", how="left")
    runs = runs[runs["entry_status"] == EntryStatus.STARTED].copy()
    runs["prize_log"] = np.log1p(pd.to_numeric(runs["prize_money"], errors="coerce"))

    targets = rh.merge(races[["race_id", "race_date"]], on="race_id", how="left")[
        ["race_id", "horse_id", "race_date"]
    ]

    src = runs[runs["prize_log"].notna()].sort_values(
        ["horse_id", "race_date"], kind="stable"
    ).copy()
    g = src.groupby("horse_id", sort=False)
    src["asof_prize_avg"] = (
        g["prize_log"].expanding().mean().reset_index(level=0, drop=True)
    )
    t = targets.sort_values("race_date", kind="stable")
    out = pd.merge_asof(
        t,
        src[["horse_id", "race_date", "asof_prize_avg"]].sort_values(
            "race_date", kind="stable"
        ),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    out["asof_prize_avg"] = out["asof_prize_avg"].astype("float64")
    return out[["race_id", "horse_id", *RACE_LEVEL_ASOF_COLUMNS]]
