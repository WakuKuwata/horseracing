"""Feature 031: race pace scenario / field-composition features (leak-safe).

The repo's first FIELD-COMPOSITION feature ("who this horse runs against = the race shape").
Each horse's as-of dominant running style (front_runner_rate / closer_rate / rel_corner_pos_avg)
is ALREADY computed strictly-before the target race by Feature 023 `build_pace_features` (same-day
excluded, merge_asof allow_exact_matches=False). This module takes ONLY that per-horse as-of output
and aggregates it WITHIN the target race_id with a LEAVE-ONE-OUT (self excluded) mean over the
STARTED field, then forms own-style × field-composition interactions.

Leak boundary (constitution II): the field aggregate uses only OTHER horses' STRICTLY-BEFORE as-of
style (never their current-race result) — what a human handicapper reads off the entry sheet. This
module never reads the target race's own running-style / corner-order / finish / result-status raw
columns (those live in build_pace_features's as-of mechanism). Missing → NaN (never 0-filled);
`field_style_coverage` surfaces how much of the field has a known style. All columns float64.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames
from .pace_features import build_pace_features

_KEYS = ["race_id", "horse_id"]

#: per-horse as-of style columns (from 023) aggregated across the field.
_STYLE_COLS = ["front_runner_rate", "closer_rate", "rel_corner_pos_avg"]

PACE_SCENARIO_COLUMNS = [
    "field_front_rate_ex_self", "field_closer_rate_ex_self", "pace_imbalance_ex_self",
    "front_pressure", "closer_setup", "style_mismatch", "field_style_coverage",
]


def _loo_mean(df: pd.DataFrame, col: str) -> pd.Series:
    """Leave-one-out mean of `col` over the STARTED field, per race_id.

    For each row: mean over (started, non-null) values of the SAME race excluding this row's own
    value (only when this row is itself started & non-null). Other-horses non-null count 0 → NaN.
    """
    started_val = df[col].where(df["is_started"] == 1)        # NaN unless started
    s_sum = df.assign(_v=started_val).groupby("race_id")["_v"].transform("sum")   # skips NaN
    s_cnt = df.assign(_v=started_val).groupby("race_id")["_v"].transform("count")  # non-null count
    self_in = (df["is_started"] == 1) & df[col].notna()
    own = df[col].where(self_in, 0.0)
    ex_sum = s_sum - own
    ex_cnt = s_cnt - self_in.astype(int)
    return pd.Series(np.where(ex_cnt > 0, ex_sum / ex_cnt, np.nan), index=df.index)


def build_pace_scenario_features(
    frames: Frames, *, pace: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-031 pace-scenario columns. Field aggregates are leave-one-out
    over the STARTED field; input is the 023 as-of style output only (no raw current-race columns).

    `pace` may be passed (precomputed build_pace_features output) to avoid recomputation when called
    from the materialize chain; otherwise it is computed here.
    """
    if pace is None:
        pace = build_pace_features(frames)
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    df = pace.merge(rh, on=_KEYS, how="left").reset_index(drop=True)
    df["is_started"] = (df["entry_status"] == EntryStatus.STARTED).astype(int)

    field_front = _loo_mean(df, "front_runner_rate")
    field_closer = _loo_mean(df, "closer_rate")
    field_corner = _loo_mean(df, "rel_corner_pos_avg")

    out = df[_KEYS].copy()
    out["field_front_rate_ex_self"] = field_front
    out["field_closer_rate_ex_self"] = field_closer
    out["pace_imbalance_ex_self"] = field_front - field_closer
    out["front_pressure"] = df["front_runner_rate"] * field_front
    out["closer_setup"] = df["closer_rate"] * field_front
    out["style_mismatch"] = df["rel_corner_pos_avg"] - field_corner

    # coverage = started horses with a known style / started field size (NOT leave-one-out).
    field_size = df.groupby("race_id")["is_started"].transform("sum")
    known = df.assign(
        _k=df["front_runner_rate"].where(df["is_started"] == 1)
    ).groupby("race_id")["_k"].transform("count")
    out["field_style_coverage"] = np.where(field_size > 0, known / field_size, np.nan)

    out[PACE_SCENARIO_COLUMNS] = out[PACE_SCENARIO_COLUMNS].astype("float64")
    return out[[*_KEYS, *PACE_SCENARIO_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(
        drop=True
    )
