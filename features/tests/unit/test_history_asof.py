"""US1 (SC-001/SC-003): leak-safe as-of history (the critical test)."""

from __future__ import annotations

import pandas as pd

from horseracing_features.history import build_history_features
from tests._frames import make_frames


def _row(frames, race_id, horse_id):
    h = build_history_features(frames)
    return h[(h.race_id == race_id) & (h.horse_id == horse_id)].iloc[0]


_BASE = [
    {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [{"horse_id": "H", "finish_order": 5}]},
    {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [{"horse_id": "H", "finish_order": 3}]},  # target
]


def test_baseline_uses_only_past():
    r = _row(make_frames(_BASE), "200802010101", "H")
    assert r.career_starts == 1
    assert r.avg_finish == 5.0
    assert r.prev_finish == 5
    assert r.days_since_last == 31


def test_future_and_sameday_do_not_leak():
    leaky = _BASE + [
        # future win (after the target)
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [{"horse_id": "H", "finish_order": 1}]},
        # same-day win (same date as the target, different race)
        {"race_id": "200802010102", "race_date": "2008-02-01", "horses": [{"horse_id": "H", "finish_order": 1}]},
    ]
    r = _row(make_frames(leaky), "200802010101", "H")
    # identical to baseline: future + same-day excluded across ALL history features
    assert r.career_starts == 1
    assert r.avg_finish == 5.0
    assert r.win_rate == 0.0
    assert r.prev_finish == 5
    assert r.days_since_last == 31


def test_completion_excluded_but_counted_as_start():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01",
         "horses": [{"horse_id": "H", "result_status": "stopped", "finish_order": None}]},  # DNF
        {"race_id": "200801080101", "race_date": "2008-01-08", "horses": [{"horse_id": "H", "finish_order": 2}]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [{"horse_id": "H", "finish_order": 1}]},  # target
    ]
    r = _row(make_frames(specs), "200802010101", "H")
    assert r.career_starts == 2          # stopped + finished both started
    assert r.avg_finish == 2.0           # only the finished race
    assert r.stop_count == 1
    assert r.prev_finish == 2            # most recent finished


def test_dns_not_counted_as_start():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01",
         "horses": [{"horse_id": "H", "entry_status": "cancelled", "result_status": None}]},  # DNS
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [{"horse_id": "H", "finish_order": 1}]},  # target
    ]
    r = _row(make_frames(specs), "200802010101", "H")
    assert r.career_starts == 0          # cancelled is not a start
    assert r.cancel_count == 1           # but recorded in the count series (not 0-filled away)
    assert pd.isna(r.avg_finish)         # no finished history -> Unknown
