"""Feature 059: within-race relative-ability features (leak-safe).

The ability version of Feature 031 (pace_scenario). Each horse's as-of ability columns (win_rate,
recent_win_rate, place/show rate, distance/surface aptitude, time/last-3f ability, jockey/trainer
form) are ALREADY computed strictly-before the target race by the upstream as-of blocks (history /
020 / 023 / 030). This module takes ONLY that per-horse as-of output and expresses each value
RELATIVE TO the STARTED field of the same race:

  * ``<col>_vs_field``  = self − leave-one-out mean of the started field (031 ``_loo_mean`` reused).
  * ``<col>_field_rank`` = within-race percentile rank over the started field (win_rate, rel_time).

Motivation: the production objective (pl_topk) is a race-internal softmax, so a race-CONSTANT
feature cancels (zero gradient). These columns vary PER HORSE (a horse's standing relative to its
rivals), giving the trees the within-race context they cannot compute per-row from absolute values.

Leak boundary (constitution II): inputs are strictly-before as-of columns only. The field aggregate
uses only OTHER started horses' as-of ability (never any current-race result/odds/same-day value) —
what a handicapper reads off the entry sheet. No raw source column is read here (pure within-race
post-processing of the merged as-of frame) → source_fingerprint unchanged, materialization-safe
(per-race deterministic, pool-end independent). Missing → NaN (never 0-filled). All columns float64.
"""

from __future__ import annotations

import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames
from .pace_scenario_features import _loo_mean

_KEYS = ["race_id", "horse_id"]

#: as-of ability columns relativized as leave-one-out deviations (`<col>_vs_field`).
_DEV_INPUTS = [
    "win_rate", "recent_win_rate", "place_rate", "show_rate",
    "dist_band_win_rate", "surface_win_rate",
    "rel_time_avg", "rel_last3f_avg", "finish_diff_best",
    "jockey_win_rate", "trainer_win_rate",
]
#: core axes given a within-field percentile rank (`<col>_field_rank`): overall ability + speed.
#: (venue_win_rate is EXCLUDED — 11% input coverage → inert; research D1.)
_RANK_INPUTS = ["win_rate", "rel_time_avg"]

RELATIVE_ABILITY_COLUMNS = (
    [f"{c}_vs_field" for c in _DEV_INPUTS] + [f"{c}_field_rank" for c in _RANK_INPUTS]
)


def _field_rank(df: pd.DataFrame, col: str) -> pd.Series:
    """Within-race percentile rank of `col` over the STARTED field.

    Non-started rows are masked to NaN BEFORE ranking so the denominator is the started population
    only (mirrors `_loo_mean`'s started filter; a full-frame rank would let scratched horses in).
    NaN inputs stay NaN (not ranked). Ties → average rank (pandas default, deterministic).
    """
    masked = df[col].where(df["is_started"] == 1)
    return df.assign(_v=masked).groupby("race_id")["_v"].rank(pct=True)


def build_relative_ability_features(frames: Frames, *, ability_frame: pd.DataFrame) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-059 relative-ability columns.

    ``ability_frame`` is the merged as-of feature frame (build_asof_features' ``out``), already
    containing every ``_DEV_INPUTS`` / ``_RANK_INPUTS`` column. It includes non-started horses, so
    entry_status is merged from ``frames.race_horses`` to derive ``is_started`` (required by
    ``_loo_mean`` and the rank mask). No raw current-race column is read.
    """
    cols = [*_KEYS, *sorted(set(_DEV_INPUTS) | set(_RANK_INPUTS))]
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    df = ability_frame[cols].merge(rh, on=_KEYS, how="left").reset_index(drop=True)
    df["is_started"] = (df["entry_status"] == EntryStatus.STARTED).astype(int)

    out = df[_KEYS].copy()
    for col in _DEV_INPUTS:
        out[f"{col}_vs_field"] = df[col] - _loo_mean(df, col)
    for col in _RANK_INPUTS:
        out[f"{col}_field_rank"] = _field_rank(df, col)

    out[RELATIVE_ABILITY_COLUMNS] = out[RELATIVE_ABILITY_COLUMNS].astype("float64")
    return out[[*_KEYS, *RELATIVE_ABILITY_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(
        drop=True
    )
