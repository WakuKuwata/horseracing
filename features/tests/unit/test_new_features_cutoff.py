"""T007 (020): new horse features are as-of (cutoff) + Unknown for debut (SC-001/SC-003)."""

from __future__ import annotations

import math

import pandas as pd

from horseracing_features.extra_features import build_extra_features
from tests._frames import make_frames


def _row(builder, frames, race_id, horse_id):
    df = builder(frames)
    return df[(df.race_id == race_id) & (df.horse_id == horse_id)].iloc[0]


# H runs 3 prior finished races, then the target; a FUTURE race exists too.
_SPECS = [
    {"race_id": "200801010101", "race_date": "2008-01-01", "distance": 1600, "track_type": "芝",
     "horses": [{"horse_id": "H", "finish_order": 4}]},
    {"race_id": "200802010101", "race_date": "2008-02-01", "distance": 1600, "track_type": "芝",
     "horses": [{"horse_id": "H", "finish_order": 2}]},
    {"race_id": "200803010101", "race_date": "2008-03-01", "distance": 1600, "track_type": "芝",
     "horses": [{"horse_id": "H", "finish_order": 1}]},
    {"race_id": "200804010101", "race_date": "2008-04-01", "distance": 1600, "track_type": "芝",
     "horses": [{"horse_id": "H", "finish_order": 3}]},   # TARGET
    {"race_id": "200805010101", "race_date": "2008-05-01", "distance": 1600, "track_type": "芝",
     "horses": [{"horse_id": "H", "finish_order": 1}]},   # FUTURE (must not leak)
]
_TARGET = "200804010101"


def test_recent_form_uses_only_past():
    r = _row(build_extra_features, make_frames(_SPECS), _TARGET, "H")
    # last 3 finishes before the target = [4, 2, 1] → mean 2.333
    assert math.isclose(r.avg_last3_finish, (4 + 2 + 1) / 3, rel_tol=1e-9)
    # surface (芝) win rate before target = 1 win / 3 finished = 0.333
    assert math.isclose(r.surface_win_rate, 1 / 3, rel_tol=1e-9)


def test_future_race_does_not_leak():
    full = _row(build_extra_features, make_frames(_SPECS), _TARGET, "H")
    no_future = _row(build_extra_features, make_frames(_SPECS[:-1]), _TARGET, "H")
    assert full.avg_last3_finish == no_future.avg_last3_finish
    assert full.surface_win_rate == no_future.surface_win_rate
    assert full.dist_band_win_rate == no_future.dist_band_win_rate


def test_changing_target_day_result_does_not_change_features():
    base = _row(build_extra_features, make_frames(_SPECS), _TARGET, "H")
    mutated = [dict(s) for s in _SPECS]
    # change the TARGET race's own finish (a result that must not enter its own features)
    mutated[3] = {**mutated[3], "horses": [{"horse_id": "H", "finish_order": 8}]}
    after = _row(build_extra_features, make_frames(mutated), _TARGET, "H")
    assert base.avg_last3_finish == after.avg_last3_finish
    assert base.surface_win_rate == after.surface_win_rate


def test_debut_is_unknown_not_zero():
    debut = [{"race_id": "200804010101", "race_date": "2008-04-01", "distance": 1600,
              "track_type": "芝", "horses": [{"horse_id": "NEW", "finish_order": 1}]}]
    r = _row(build_extra_features, make_frames(debut), "200804010101", "NEW")
    assert pd.isna(r.avg_last3_finish)      # Unknown, NOT 0
    assert pd.isna(r.surface_win_rate)
    assert pd.isna(r.dist_band_win_rate)
