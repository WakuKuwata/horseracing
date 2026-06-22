"""US1 (SC-002): debut null != 0, flags correct."""

from __future__ import annotations

import pandas as pd

from horseracing_features.history import build_history_features
from tests._frames import make_frames


def _row(frames, race_id, horse_id):
    h = build_history_features(frames)
    return h[(h.race_id == race_id) & (h.horse_id == horse_id)].iloc[0]


def test_debut_past_features_null_not_zero():
    specs = [{"race_id": "200802010101", "race_date": "2008-02-01",
              "horses": [{"horse_id": "H", "finish_order": 1}]}]
    r = _row(make_frames(specs), "200802010101", "H")
    assert r.career_starts == 0
    assert r.is_debut == 1 and r.has_past_race == 0 and r.past_race_count == 0
    assert pd.isna(r.avg_finish)
    assert pd.isna(r.win_rate)
    assert pd.isna(r.prev_finish)
    assert pd.isna(r.prev_last3f)
    assert pd.isna(r.days_since_last)


def test_low_history_flag():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [{"horse_id": "H", "finish_order": 3}]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [{"horse_id": "H", "finish_order": 1}]},  # 1 prior
    ]
    r = _row(make_frames(specs), "200802010101", "H")
    assert r.career_starts == 1
    assert r.is_low_history == 1
    assert r.is_debut == 0
    assert r.has_past_race == 1
