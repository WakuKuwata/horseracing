"""Feature 059 correctness: leave-one-out deviation + within-field percentile rank.

Inputs are a synthetic `ability_frame` (the merged as-of frame) so the test pins the within-race
relativization logic, not the upstream as-of computation. Non-started horses must be excluded from
BOTH the LOO field mean (via `_loo_mean`) and the rank denominator (started-only mask, research D3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from horseracing_features.relative_ability_features import (
    _DEV_INPUTS,
    RELATIVE_ABILITY_COLUMNS,
    build_relative_ability_features,
)
from tests._frames import make_frames

TARGET = "200803010101"
_ALL_INPUTS = sorted(set(_DEV_INPUTS) | {"win_rate", "rel_time_avg"})


def _ability_frame(rows: dict[str, dict]) -> pd.DataFrame:
    """rows: horse_id -> {input_col: value}. Missing input cols -> NaN."""
    recs = []
    for hid, vals in rows.items():
        rec = {"race_id": TARGET, "horse_id": hid}
        for c in _ALL_INPUTS:
            rec[c] = vals.get(c, np.nan)
        recs.append(rec)
    return pd.DataFrame(recs)


def _frames(horse_ids, scratched=()):
    horses = [
        {"horse_id": h, "finish_order": i + 1,
         "entry_status": EntryStatus.CANCELLED if h in scratched else EntryStatus.STARTED}
        for i, h in enumerate(horse_ids)
    ]
    return make_frames([{"race_id": TARGET, "race_date": "2008-03-01", "horses": horses}])


def _run(rows, scratched=()):
    frames = _frames(list(rows), scratched=scratched)
    af = _ability_frame(rows)
    return build_relative_ability_features(frames, ability_frame=af).set_index(
        ["race_id", "horse_id"]
    )


def test_deviation_is_self_minus_loo_mean():
    rows = {"A": {"win_rate": 0.4}, "B": {"win_rate": 0.2}, "C": {"win_rate": 0.1}}
    out = _run(rows)
    # A: 0.4 − mean(0.2, 0.1) = 0.4 − 0.15
    assert abs(out.loc[(TARGET, "A"), "win_rate_vs_field"] - (0.4 - 0.15)) < 1e-12
    assert abs(out.loc[(TARGET, "C"), "win_rate_vs_field"] - (0.1 - 0.3)) < 1e-12


def test_single_starter_deviation_nan_rank_degenerate():
    out = _run({"A": {"win_rate": 0.4}})
    assert pd.isna(out.loc[(TARGET, "A"), "win_rate_vs_field"])   # no others -> NaN
    assert out.loc[(TARGET, "A"), "win_rate_field_rank"] == 1.0   # pandas default (accepted)


def test_all_nan_field_nan():
    out = _run({"A": {}, "B": {}, "C": {}})  # every input NaN
    assert pd.isna(out.loc[(TARGET, "A"), "win_rate_vs_field"])
    assert pd.isna(out.loc[(TARGET, "A"), "win_rate_field_rank"])


def test_nan_horse_excluded_from_denominator():
    # B has no win_rate -> A's LOO field is C only.
    rows = {"A": {"win_rate": 0.4}, "B": {}, "C": {"win_rate": 0.2}}
    out = _run(rows)
    assert abs(out.loc[(TARGET, "A"), "win_rate_vs_field"] - (0.4 - 0.2)) < 1e-12
    assert pd.isna(out.loc[(TARGET, "B"), "win_rate_vs_field"])  # self NaN -> NaN


def test_field_rank_percentile_and_ties():
    rows = {"A": {"win_rate": 0.3}, "B": {"win_rate": 0.3}, "C": {"win_rate": 0.1}}
    out = _run(rows)
    # C lowest -> pct 1/3; A,B tie top -> average rank (2,3)->2.5 -> 2.5/3
    assert abs(out.loc[(TARGET, "C"), "win_rate_field_rank"] - (1.0 / 3.0)) < 1e-12
    assert abs(out.loc[(TARGET, "A"), "win_rate_field_rank"] - (2.5 / 3.0)) < 1e-12


def test_non_started_excluded_from_rank_and_loo():
    # D is scratched (CANCELLED): excluded from A's LOO field AND from the rank denominator.
    rows = {"A": {"win_rate": 0.4}, "B": {"win_rate": 0.2},
            "C": {"win_rate": 0.1}, "D": {"win_rate": 0.9}}
    out = _run(rows, scratched=("D",))
    # A's field = B,C only (D excluded): 0.4 − mean(0.2,0.1)
    assert abs(out.loc[(TARGET, "A"), "win_rate_vs_field"] - (0.4 - 0.15)) < 1e-12
    # rank denominator = started {A,B,C}: A is top of 3 -> 3/3
    assert abs(out.loc[(TARGET, "A"), "win_rate_field_rank"] - 1.0) < 1e-12
    # D (non-started) -> NaN rank (masked out), and NOT counted anywhere
    assert pd.isna(out.loc[(TARGET, "D"), "win_rate_field_rank"])


def test_all_columns_present_and_float64():
    rows = {"A": {c: 0.3 for c in _ALL_INPUTS}, "B": {c: 0.1 for c in _ALL_INPUTS}}
    out = _run(rows)
    assert list(out.columns) == RELATIVE_ABILITY_COLUMNS
    assert len(RELATIVE_ABILITY_COLUMNS) == 13
    for c in RELATIVE_ABILITY_COLUMNS:
        assert out[c].dtype == np.float64, c
