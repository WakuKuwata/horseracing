"""Feature 097 (T007, INV-EM3): early-mid pace uses PAST races only — leak guard in 3 directions.

Invariant under changing: (a) the target race's own finish_time/last_3f, (b) rivals' same-day
values in the target race, (c) a future-year race. Mirrors test_pace_features_leak (023).
"""

from __future__ import annotations

import pandas as pd

from horseracing_features.early_mid_pace_features import (
    EARLY_MID_PACE_COLUMNS,
    build_early_mid_pace_features,
)
from tests._frames import make_frames

_TARGET = "200803010101"


def _specs(*, h_today=(93.0, 33.0), rival_today=(97.0, 38.0), future=False, same_day=False):
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "finish_time": 94.0, "last_3f": 34.0, "finish_order": 1},
            {"horse_id": "X", "finish_time": 95.0, "last_3f": 36.0, "finish_order": 2}]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            {"horse_id": "H", "finish_time": 96.0, "last_3f": 35.0, "finish_order": 1},
            {"horse_id": "Y", "finish_time": 96.0, "last_3f": 37.0, "finish_order": 2}]},
        {"race_id": _TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "finish_time": h_today[0], "last_3f": h_today[1], "finish_order": 1},
            {"horse_id": "Z", "finish_time": rival_today[0], "last_3f": rival_today[1],
             "finish_order": 2}]},
    ]
    if same_day:   # another race on the target day with H's rival: must not enter H's features
        specs.append({"race_id": "200803010102", "race_date": "2008-03-01", "horses": [
            {"horse_id": "H", "finish_time": 60.0, "last_3f": 20.0, "finish_order": 1},
            {"horse_id": "W", "finish_time": 70.0, "last_3f": 30.0, "finish_order": 2}]})
    if future:
        specs.append({"race_id": "200904010101", "race_date": "2009-04-01", "horses": [
            {"horse_id": "H", "finish_time": 80.0, "last_3f": 20.0, "finish_order": 1}]})
    return specs


def _row(specs):
    df = build_early_mid_pace_features(make_frames(specs))
    return df[(df.race_id == _TARGET) & (df.horse_id == "H")].iloc[0]


def _same(a, b):
    for c in EARLY_MID_PACE_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_baseline_values_from_past_only():
    r = _row(_specs())
    assert r.asof_rel_early_mid_avg == 0.75 and r.asof_rel_early_mid_best == 0.5


def test_invariant_to_targets_own_result():
    _same(_row(_specs(h_today=(80.0, 30.0))), _row(_specs(h_today=(110.0, 40.0))))


def test_invariant_to_rivals_same_day_values():
    _same(_row(_specs(rival_today=(80.0, 30.0))), _row(_specs(rival_today=(120.0, 45.0))))


def test_invariant_to_same_day_other_race():
    _same(_row(_specs()), _row(_specs(same_day=True)))


def test_invariant_to_future_race():
    _same(_row(_specs()), _row(_specs(future=True)))
