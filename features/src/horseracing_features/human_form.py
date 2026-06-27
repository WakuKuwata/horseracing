"""Feature 020: jockey / trainer recent form (cross-horse as-of win rate).

CRITICAL leak boundary (codex): a cross-horse statistic must NOT include the target row's own result
nor any same-day result (target-encoding leak). We group by jockey_id / trainer_id and use the SAME
daily (cumsum − current-day) mechanism: subtracting the whole target day removes the target mount
AND every same-day mount of that jockey/trainer. Only finished prior mounts count. Before R only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames

HUMAN_COLUMNS = ["jockey_win_rate", "trainer_win_rate"]


def _runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status", "jockey_id", "trainer_id"]]
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    runs = rh.merge(races, on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    runs["is_finished"] = (runs["result_status"] == ResultStatus.FINISHED).astype(int)
    runs["is_win"] = ((runs["is_finished"] == 1) & (runs["finish_order"] == 1)).astype(int)
    runs["finish_for_cnt"] = np.where(runs["is_finished"] == 1, 1.0, np.nan)
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    return runs


def _win_rate_before(runs: pd.DataFrame, key: str, out_col: str) -> pd.DataFrame:
    """Per (key, date) win rate over finished prior mounts, STRICTLY before that date."""
    daily = runs.groupby([key, "race_date"], as_index=False).agg(
        d_wins=("is_win", "sum"),
        d_cnt=("finish_for_cnt", "count"),
    ).sort_values([key, "race_date"], kind="stable")
    g = daily.groupby(key, sort=False)
    daily["wins_b"] = g["d_wins"].cumsum() - daily["d_wins"]   # excludes the whole current day
    daily["cnt_b"] = g["d_cnt"].cumsum() - daily["d_cnt"]
    daily[out_col] = np.where(daily["cnt_b"] > 0, daily["wins_b"] / daily["cnt_b"], np.nan)
    return daily[[key, "race_date", out_col]]


def build_human_form_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) jockey/trainer as-of win rate (target-row + same-day excluded)."""
    runs = _runs(frames)
    targets = runs[["race_id", "horse_id", "race_date", "jockey_id", "trainer_id"]].copy()

    jock = _win_rate_before(runs, "jockey_id", "jockey_win_rate")
    trn = _win_rate_before(runs, "trainer_id", "trainer_win_rate")
    # target appearance has (jockey_id, race_date) in daily → exact merge picks its before-value.
    out = (targets
           .merge(jock, on=["jockey_id", "race_date"], how="left")
           .merge(trn, on=["trainer_id", "race_date"], how="left"))
    return out[["race_id", "horse_id", *HUMAN_COLUMNS]]
