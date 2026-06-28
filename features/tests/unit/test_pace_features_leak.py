"""T007 (023): pace/time features use PAST races only — extended leak-guard (FR-002, SC-001).

Invariant under changing: (a) the target race's own time/last3f/diff/corner/style, (b) the target
race's rivals' today values, (c) future-year races. The in-race relative baseline for a past race is
built from that past race's finishers only — never from R, R's rivals, same-day, or the future.
"""

from __future__ import annotations

import pandas as pd

from horseracing_features.pace_features import PACE_COLUMNS, build_pace_features
from tests._frames import make_frames

_TARGET = "200803010101"


def _specs(*, h_today=33.0, rival_today=33.0, future=False):
    specs = [
        # past R1: H is 1s faster than the race mean (last3f 34 vs rival 36 -> mean 35 -> rel -1)
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "last_3f": 34.0, "finish_time": 94.0, "finish_time_diff": 0.0,
             "corner_orders": ["2"], "running_style": "先行", "finish_order": 1},
            {"horse_id": "X", "last_3f": 36.0, "finish_time": 95.0, "finish_time_diff": 1.0,
             "corner_orders": ["5"], "running_style": "差し", "finish_order": 2},
        ]},
        # past R2: H 1s faster than mean again (35 vs 37 -> mean 36 -> rel -1)
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            {"horse_id": "H", "last_3f": 35.0, "finish_time": 95.0, "finish_time_diff": 0.0,
             "corner_orders": ["3"], "running_style": "先行", "finish_order": 1},
            {"horse_id": "Y", "last_3f": 37.0, "finish_time": 96.0, "finish_time_diff": 1.0,
             "corner_orders": ["6"], "running_style": "追込", "finish_order": 2},
        ]},
        # TARGET R3: H + rival Z — their TODAY values must never enter H's features
        {"race_id": _TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "last_3f": h_today, "finish_time": 90.0 + h_today, "finish_time_diff": 0.0,
             "corner_orders": ["1"], "running_style": "逃げ", "finish_order": 1},
            {"horse_id": "Z", "last_3f": rival_today, "finish_time": 90.0 + rival_today,
             "finish_time_diff": 2.0, "corner_orders": ["8"], "running_style": "追込",
             "finish_order": 2},
        ]},
    ]
    if future:
        specs.append({"race_id": "200904010101", "race_date": "2009-04-01", "horses": [
            {"horse_id": "H", "last_3f": 20.0, "finish_time": 80.0, "finish_time_diff": 0.0,
             "corner_orders": ["1"], "running_style": "逃げ", "finish_order": 1},
        ]})
    return specs


def _row(specs):
    df = build_pace_features(make_frames(specs))
    return df[(df.race_id == _TARGET) & (df.horse_id == "H")].iloc[0]


def test_features_computed_from_past():
    r = _row(_specs())
    assert r.rel_last3f_avg == -1.0          # mean of (-1, -1)
    assert r.rel_last3f_best == -1.0
    assert not pd.isna(r.rel_time_avg)


def test_invariant_to_targets_own_result():
    a = _row(_specs(h_today=30.0))
    b = _row(_specs(h_today=40.0))
    for c in PACE_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_rival_today_values():
    a = _row(_specs(rival_today=30.0))
    b = _row(_specs(rival_today=45.0))
    for c in PACE_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_future_races():
    a = _row(_specs(future=False))
    b = _row(_specs(future=True))
    for c in PACE_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c
