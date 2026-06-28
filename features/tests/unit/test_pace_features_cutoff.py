"""T008 (023): debut → Unknown(null, not 0); non-finisher past races excluded (FR-004/005, SC-002)."""

from __future__ import annotations

import pandas as pd

from horseracing_features.pace_features import PACE_TIME_COLUMNS, build_pace_features
from tests._frames import make_frames

_TARGET = "200803010101"


def _row(specs, horse="H"):
    df = build_pace_features(make_frames(specs))
    sub = df[(df.race_id == _TARGET) & (df.horse_id == horse)]
    return sub.iloc[0]


def test_debut_horse_is_unknown_not_zero():
    specs = [{"race_id": _TARGET, "race_date": "2008-03-01",
              "horses": [{"horse_id": "H", "last_3f": 34.0, "finish_order": 1}]}]
    r = _row(specs)
    for c in PACE_TIME_COLUMNS:
        assert pd.isna(r[c]), f"{c} must be Unknown (null) for a debut horse, not 0"


def test_non_finisher_past_race_excluded():
    # H's only past appearance was a cancellation (no result) -> no pace history -> Unknown
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01",
         "horses": [{"horse_id": "H", "entry_status": "cancelled"}]},
        {"race_id": _TARGET, "race_date": "2008-03-01",
         "horses": [{"horse_id": "H", "last_3f": 34.0, "finish_order": 1},
                    {"horse_id": "Z", "last_3f": 36.0, "finish_order": 2}]},
    ]
    r = _row(specs)
    assert pd.isna(r.rel_last3f_avg)  # cancelled past race contributes nothing


def test_only_past_finishers_counted():
    # one finished past race (rel -1) + one cancelled past race -> avg over the single finish
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            {"horse_id": "H", "last_3f": 34.0, "finish_order": 1},
            {"horse_id": "X", "last_3f": 36.0, "finish_order": 2}]},
        {"race_id": "200802010101", "race_date": "2008-02-01",
         "horses": [{"horse_id": "H", "entry_status": "cancelled"}]},
        {"race_id": _TARGET, "race_date": "2008-03-01",
         "horses": [{"horse_id": "H", "last_3f": 34.0, "finish_order": 1},
                    {"horse_id": "Z", "last_3f": 36.0, "finish_order": 2}]},
    ]
    r = _row(specs)
    assert r.rel_last3f_avg == -1.0  # only the finished R1 counts
