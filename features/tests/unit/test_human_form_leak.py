"""T008 (020): jockey/trainer form excludes target row + same-day (SC-002)."""

from __future__ import annotations

import math

import pandas as pd

from horseracing_features.human_form import build_human_form_features
from tests._frames import make_frames


def _row(frames, race_id, horse_id):
    df = build_human_form_features(frames)
    return df[(df.race_id == race_id) & (df.horse_id == horse_id)].iloc[0]


# jockey J1 rides: 2 prior finished mounts (1 win), a SAME-DAY other mount, and the target mount.
def _specs(target_other_finish, sameday_finish):
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01",
         "horses": [{"horse_id": "A", "jockey_id": "J1", "trainer_id": "T1", "finish_order": 1}]},
        {"race_id": "200802010101", "race_date": "2008-02-01",
         "horses": [{"horse_id": "B", "jockey_id": "J1", "trainer_id": "T1", "finish_order": 5}]},
        # TARGET day 2008-03-01: target mount H + a same-day other mount C (both J1)
        {"race_id": "200803010101", "race_date": "2008-03-01",
         "horses": [{"horse_id": "H", "jockey_id": "J1", "trainer_id": "T1",
                     "finish_order": target_other_finish}]},
        {"race_id": "200803010102", "race_date": "2008-03-01",
         "horses": [{"horse_id": "C", "jockey_id": "J1", "trainer_id": "T1",
                     "finish_order": sameday_finish}]},
    ]


def test_jockey_winrate_before_only():
    r = _row(make_frames(_specs(1, 1)), "200803010101", "H")
    # before 2008-03-01: 2 finished mounts (1-2-01 win, 2-2-01 5th) → 1/2 = 0.5
    assert math.isclose(r.jockey_win_rate, 0.5, rel_tol=1e-9)
    assert math.isclose(r.trainer_win_rate, 0.5, rel_tol=1e-9)


def test_target_row_result_excluded():
    a = _row(make_frames(_specs(1, 9)), "200803010101", "H")   # target H wins
    b = _row(make_frames(_specs(9, 9)), "200803010101", "H")   # target H loses
    assert a.jockey_win_rate == b.jockey_win_rate              # target's own result never counts


def test_same_day_other_mount_excluded():
    a = _row(make_frames(_specs(1, 1)), "200803010101", "H")   # same-day C wins
    b = _row(make_frames(_specs(1, 9)), "200803010101", "H")   # same-day C loses
    assert a.jockey_win_rate == b.jockey_win_rate              # same-day mounts never count


def test_debut_jockey_unknown():
    specs = [{"race_id": "200803010101", "race_date": "2008-03-01",
              "horses": [{"horse_id": "H", "jockey_id": "JNEW", "trainer_id": "TNEW",
                          "finish_order": 1}]}]
    r = _row(make_frames(specs), "200803010101", "H")
    assert pd.isna(r.jockey_win_rate) and pd.isna(r.trainer_win_rate)   # Unknown, not 0
