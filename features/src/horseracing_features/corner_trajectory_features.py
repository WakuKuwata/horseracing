"""Feature 041: corner-trajectory — past-race position DELTAS, as-of aggregated.

023 pace/position captures position LEVELS (average final-corner position, style rates).
This module captures position CHANGE per past run: straight-line gain (final corner ->
finishing position = 直線の伸び), early position (先行度), and the biggest corner-to-corner
improvement (捲り). Each raw score is normalized by THAT past race's started field size,
then expanding-aggregated per horse and attached to the target row via
``merge_asof(direction=backward, allow_exact_matches=False)`` — strictly-before with the
same-day excluded (same mechanism as 023). The target race's own passing order and
finishing position never enter its features (constitution II). NaN propagates: a horse
with no valid past trajectory gets NaN, never 0 (Unknown != 0).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames

CORNER_TRAJECTORY_COLUMNS = [
    "asof_late_gain_avg",
    "asof_late_gain_best",
    "asof_early_pos_avg",
    "asof_mid_move_avg",
]

_RAW = ("late_gain", "early_pos", "mid_move")


def _corner_positions(orders) -> list[float] | None:
    """Numeric corner passing positions, or None when absent/unparseable."""
    if isinstance(orders, (list, tuple)) and len(orders) > 0:
        try:
            return [float(x) for x in orders]
        except (TypeError, ValueError):
            return None
    return None


def _mid_improvement(pos: list[float] | None) -> float:
    """Largest consecutive corner-to-corner position gain (捲り); NaN if < 2 corners."""
    if not pos or len(pos) < 2:
        return np.nan
    return max(pos[j] - pos[j + 1] for j in range(len(pos) - 1))


def _traj_runs(frames: Frames) -> pd.DataFrame:
    """Per-(race, horse) run pool with raw trajectory scores (valid on finished starters)."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    rr = frames.race_results[
        ["race_id", "horse_id", "finish_order", "result_status", "corner_orders"]
    ]
    runs = rh.merge(races, on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    fs = runs.groupby("race_id", as_index=False)["is_started"].sum().rename(
        columns={"is_started": "field_size"}
    )
    runs = runs.merge(fs, on="race_id", how="left")

    pos = runs["corner_orders"].map(_corner_positions)
    corner_first = pos.map(lambda p: p[0] if p else np.nan).astype("float64")
    corner_last = pos.map(lambda p: p[-1] if p else np.nan).astype("float64")
    mid_imp = pos.map(_mid_improvement).astype("float64")

    fo = pd.to_numeric(runs["finish_order"], errors="coerce")
    fsz = pd.to_numeric(runs["field_size"], errors="coerce").astype("float64")
    ok = (
        (runs["result_status"] == ResultStatus.FINISHED)
        & (runs["is_started"] == 1)
        & (fsz > 0)
    )
    runs["late_gain"] = np.where(ok, (corner_last - fo) / fsz, np.nan)
    runs["early_pos"] = np.where(ok, corner_first / fsz, np.nan)
    runs["mid_move"] = np.where(ok, mid_imp / fsz, np.nan)
    return runs


def build_corner_trajectory_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-041 columns. All as-of race_date < target (same-day
    excluded); the cumulative state attached to a past run already includes that run, so the
    backward as-of merge yields exactly the strictly-before aggregate for the target row."""
    runs = _traj_runs(frames)

    src = runs.sort_values(["horse_id", "race_date", "race_id"], kind="stable").copy()
    grp = src.groupby("horse_id", sort=False)
    cum_cols: list[str] = []
    for col in _RAW:
        vals = src[col].astype("float64")
        csum = vals.fillna(0.0).groupby(src["horse_id"], sort=False).cumsum()
        ccnt = vals.notna().astype("float64").groupby(src["horse_id"], sort=False).cumsum()
        src[f"cum_{col}_avg"] = np.where(ccnt > 0, csum / ccnt, np.nan)
        cum_cols.append(f"cum_{col}_avg")
    src["cum_late_gain_best"] = grp["late_gain"].cummax()
    cum_cols.append("cum_late_gain_best")

    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    t = targets.sort_values("race_date", kind="stable")
    merged = pd.merge_asof(
        t,
        src[["horse_id", "race_date", *cum_cols]].sort_values("race_date", kind="stable"),
        on="race_date",
        by="horse_id",
        direction="backward",
        allow_exact_matches=False,
    )

    out = merged.rename(
        columns={
            "cum_late_gain_avg": "asof_late_gain_avg",
            "cum_late_gain_best": "asof_late_gain_best",
            "cum_early_pos_avg": "asof_early_pos_avg",
            "cum_mid_move_avg": "asof_mid_move_avg",
        }
    )
    out = out[["race_id", "horse_id", *CORNER_TRAJECTORY_COLUMNS]]
    out[CORNER_TRAJECTORY_COLUMNS] = out[CORNER_TRAJECTORY_COLUMNS].astype("float64")
    return out.reset_index(drop=True)
