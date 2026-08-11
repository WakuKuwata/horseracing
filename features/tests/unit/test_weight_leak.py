"""Leak guard for the strictly-before previous body-weight feature."""

from __future__ import annotations

import pandas as pd

from horseracing_features.weight_history_features import build_weight_history_features
from tests._frames import make_frames

_TARGET = "200803010101"
_SAME_DAY = "200803010102"


def _specs():
    return [
        {
            "race_id": "200801010101",
            "race_date": "2008-01-01",
            "horses": [{"horse_id": "H", "weight": 450, "odds": 8.0, "finish_order": 2}],
        },
        {
            "race_id": "200802010101",
            "race_date": "2008-02-01",
            "horses": [{"horse_id": "H", "weight": 455, "odds": 5.0, "finish_order": 1}],
        },
        {
            "race_id": _TARGET,
            "race_date": "2008-03-01",
            "horses": [
                {"horse_id": "H", "weight": 460, "odds": 2.0, "finish_order": 1},
                {"horse_id": "D", "weight": None, "odds": 10.0, "finish_order": 2},
            ],
        },
        {
            "race_id": _SAME_DAY,
            "race_date": "2008-03-01",
            "horses": [{"horse_id": "H", "weight": 600, "odds": 3.0, "finish_order": 3}],
        },
    ]


def _target_rows(frames):
    return build_weight_history_features(frames, target_race_ids=frozenset({_TARGET})).set_index(
        ["race_id", "horse_id"]
    )


def _assert_unchanged(frames):
    pd.testing.assert_frame_equal(
        _target_rows(make_frames(_specs())), _target_rows(frames), check_exact=True
    )


def test_invariant_to_target_race_results():
    changed = make_frames(_specs())
    target_results = changed.race_results["race_id"] == _TARGET
    changed.race_results.loc[target_results, "finish_order"] = [9, 1]
    _assert_unchanged(changed)


def test_invariant_to_current_race_odds():
    changed = make_frames(_specs())
    target_entries = changed.race_horses["race_id"] == _TARGET
    changed.race_horses.loc[target_entries, "odds"] = [1.1, 99.0]
    changed.race_horses.loc[target_entries, "popularity"] = [1, 18]
    _assert_unchanged(changed)


def test_invariant_to_same_day_other_race():
    changed = make_frames(_specs())
    same_day_entry = changed.race_horses["race_id"] == _SAME_DAY
    changed.race_horses.loc[same_day_entry, "weight"] = 150
    changed.race_horses.loc[same_day_entry, "odds"] = 1.1
    same_day_result = changed.race_results["race_id"] == _SAME_DAY
    changed.race_results.loc[same_day_result, "finish_order"] = 1
    _assert_unchanged(changed)
