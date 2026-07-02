"""Feature 041 US2: leak boundary — target-race / same-day / future invariance + grep."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from horseracing_features.corner_trajectory_features import (
    CORNER_TRAJECTORY_COLUMNS,
    build_corner_trajectory_features,
)
from tests._frames import make_frames

_SRC = (
    Path(__file__).resolve().parents[2]
    / "src" / "horseracing_features" / "corner_trajectory_features.py"
)


def _specs():
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["5", "3"],
             "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["1", "1"],
             "finish_order": 2}]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["2", "2"],
             "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["9", "9"],
             "finish_order": 2}]},
    ]


def _target_rows(frames):
    out = build_corner_trajectory_features(frames)
    return out[out.race_id == "200803010101"].set_index("horse_id").sort_index()


def _assert_same(a: pd.DataFrame, b: pd.DataFrame):
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_invariant_to_own_current_race_result():
    # INV-L1: mutating the TARGET race's own corners/finish must not change its features
    base = _target_rows(make_frames(_specs()))
    specs = _specs()
    specs[1]["horses"][0]["corner_orders"] = ["18", "18"]
    specs[1]["horses"][0]["finish_order"] = 18
    mutated = _target_rows(make_frames(specs))
    _assert_same(base, mutated)


def test_invariant_to_same_day_other_race():
    # INV-L2: a same-day race (allow_exact_matches=False) must not leak in
    specs = _specs() + [
        {"race_id": "200803010102", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H2", "horse_number": 1, "corner_orders": ["1", "1"],
             "finish_order": 1},
            {"horse_id": "H", "horse_number": 2, "entry_status": "cancelled"}]},
    ]
    base = _target_rows(make_frames(_specs()))
    with_sameday = _target_rows(make_frames(specs))
    _assert_same(base, with_sameday.loc[base.index])


def test_invariant_to_future_race():
    # INV-L3: adding a FUTURE race must not change past rows (pool-end independent)
    specs = _specs() + [
        {"race_id": "200812010101", "race_date": "2008-12-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["1", "1"],
             "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["2", "2"],
             "finish_order": 2}]},
    ]
    base = _target_rows(make_frames(_specs()))
    with_future = _target_rows(make_frames(specs))
    _assert_same(base, with_future)


def test_source_never_references_market_tokens():
    # INV-L4: no odds/payout/dividend/popularity in the module source
    text = _SRC.read_text(encoding="utf-8").lower()
    for tok in ("odds", "payout", "dividend", "popularity"):
        assert tok not in text, tok


def test_columns_match_registry_group():
    from horseracing_features.registry import FEATURE_GROUPS

    cols = [c for c, g in FEATURE_GROUPS.items() if g == "corner_trajectory"]
    assert sorted(cols) == sorted(CORNER_TRAJECTORY_COLUMNS)
