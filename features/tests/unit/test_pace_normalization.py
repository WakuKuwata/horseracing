"""T010 (023): in-race relative normalization absorbs distance/surface level differences (SC-003)."""

from __future__ import annotations

from horseracing_features.pace_features import build_pace_features
from tests._frames import make_frames

_TARGET = "200803010101"


def _row(specs):
    df = build_pace_features(make_frames(specs))
    return df[(df.race_id == _TARGET) & (df.horse_id == "H")].iloc[0]


def test_condition_level_absorbed_by_in_race_relative():
    # Two past races with VERY different absolute last_3f levels (sprint vs long), but in BOTH H is
    # exactly the race mean (rival is symmetric around H) -> rel ≈ 0 in both -> rel_last3f_avg ≈ 0.
    # A naive RAW average would be ~ (33.5 + 37.5)/2 = 35.5 (dominated by the level difference).
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "distance": 1200, "horses": [
            {"horse_id": "H", "last_3f": 33.5, "finish_order": 1},
            {"horse_id": "A", "last_3f": 33.0, "finish_order": 2},
            {"horse_id": "B", "last_3f": 34.0, "finish_order": 3}]},   # mean 33.5 -> H rel 0
        {"race_id": "200802010101", "race_date": "2008-02-01", "distance": 2500, "horses": [
            {"horse_id": "H", "last_3f": 37.5, "finish_order": 1},
            {"horse_id": "C", "last_3f": 37.0, "finish_order": 2},
            {"horse_id": "D", "last_3f": 38.0, "finish_order": 3}]},   # mean 37.5 -> H rel 0
        {"race_id": _TARGET, "race_date": "2008-03-01",
         "horses": [{"horse_id": "H", "last_3f": 35.0, "finish_order": 1},
                    {"horse_id": "Z", "last_3f": 36.0, "finish_order": 2}]},
    ]
    r = _row(specs)
    assert abs(r.rel_last3f_avg) < 0.1                      # condition-level absorbed (≈0)
    raw_avg = (33.5 + 37.5) / 2                             # what a naive raw average would give
    assert abs(r.rel_last3f_avg) < abs(raw_avg) - 30        # far from the raw level (~35.5)
