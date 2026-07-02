"""Feature 041 US1: corner-trajectory values / expanding as-of / edges (DB-free)."""

from __future__ import annotations

import numpy as np

from horseracing_features.corner_trajectory_features import (
    CORNER_TRAJECTORY_COLUMNS,
    build_corner_trajectory_features,
)
from tests._frames import make_frames


def _row(out, rid, hid):
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _specs():
    # H: two past runs then a target; X fills the field (2 starters -> field_size=2)
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["5", "3"],
             "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["1", "1"],
             "finish_order": 2}]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["8", "6", "2"],
             "finish_order": 2},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["2", "2", "1"],
             "finish_order": 1}]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1},
            {"horse_id": "D", "horse_number": 2, "finish_order": 2}]},  # D debut
    ]


def test_values_and_expanding_aggregation():
    out = build_corner_trajectory_features(make_frames(_specs()))
    # target 2008-03: H's two past runs (field_size=2 each)
    # run1: late_gain=(3-1)/2=1.0, early=5/2=2.5, mid=(5-3)/2=1.0
    # run2: late_gain=(2-2)/2=0.0, early=8/2=4.0, mid=max(8-6,6-2)/2=2.0
    r = _row(out, "200803010101", "H")
    assert abs(r["asof_late_gain_avg"] - 0.5) < 1e-12
    assert abs(r["asof_late_gain_best"] - 1.0) < 1e-12
    assert abs(r["asof_early_pos_avg"] - 3.25) < 1e-12
    assert abs(r["asof_mid_move_avg"] - 1.5) < 1e-12


def test_strictly_before_first_run_has_nan():
    out = build_corner_trajectory_features(make_frames(_specs()))
    # H's FIRST run (2008-01): no strictly-before history -> all NaN
    r = _row(out, "200801010101", "H")
    assert all(np.isnan(r[c]) for c in CORNER_TRAJECTORY_COLUMNS)
    # H's second run (2008-02): exactly run1's values
    r2 = _row(out, "200802010101", "H")
    assert abs(r2["asof_late_gain_avg"] - 1.0) < 1e-12
    assert abs(r2["asof_early_pos_avg"] - 2.5) < 1e-12


def test_debut_horse_all_nan():
    out = build_corner_trajectory_features(make_frames(_specs()))
    r = _row(out, "200803010101", "D")
    assert all(np.isnan(r[c]) for c in CORNER_TRAJECTORY_COLUMNS)


def test_single_corner_mid_move_nan_others_valid():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["3"], "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["1"], "finish_order": 2}]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "finish_order": 2}]},
    ]
    out = build_corner_trajectory_features(make_frames(specs))
    r = _row(out, "200803010101", "H")
    assert abs(r["asof_late_gain_avg"] - (3 - 1) / 2) < 1e-12   # valid
    assert abs(r["asof_early_pos_avg"] - 1.5) < 1e-12            # valid
    assert np.isnan(r["asof_mid_move_avg"])                       # <2 corners -> NaN


def test_unparseable_corners_skipped():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "corner_orders": ["x", "y"],
             "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "corner_orders": ["1", "1"],
             "finish_order": 2}]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "finish_order": 2}]},
    ]
    out = build_corner_trajectory_features(make_frames(specs))
    r = _row(out, "200803010101", "H")
    assert all(np.isnan(r[c]) for c in CORNER_TRAJECTORY_COLUMNS)


def test_all_float64():
    out = build_corner_trajectory_features(make_frames(_specs()))
    for c in CORNER_TRAJECTORY_COLUMNS:
        assert out[c].dtype == np.float64, c
