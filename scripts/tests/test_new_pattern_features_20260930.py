"""Meaningful temporal-leakage and identity tests for the exploratory feature builder."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/new_pattern_features_20260930.py"
SPEC = importlib.util.spec_from_file_location("new_pattern_features_20260930", PATH)
features = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(features)


def sample():
    rows = []
    dates = ["2007-01-06", "2007-01-13", "2007-01-20", "2007-04-14", "2007-05-05", "2007-05-26"]
    surfaces = ["芝", "ダ", "芝", "芝", "芝", "芝"]
    venues = ["05", "06", "05", "05", "05", "05"]
    for i, date in enumerate(dates):
        rows.append(dict(race_id=f"r{i}", horse_id="A", race_date=date,
                         days_since_last=[np.nan, 7, 7, 84, 21, 21][i],
                         track_type=surfaces[i], dist_band="mile", venue_code=venues[i],
                         won=i in (0, 3), finish_order=1 if i in (0, 3) else 4,
                         result_status="finished", prev_track_type=surfaces[i - 1] if i else None,
                         prev_venue_code=venues[i - 1] if i else None,
                         prev_running_style="先行" if i else None,
                         odds=4., prev_odds=2. if i else np.nan, running_style="逃げ", sentinel=f"A{i}"))
    for i, date in enumerate(dates[:2]):
        rows.append(dict(race_id=f"r{i}", horse_id="B", race_date=date,
                         days_since_last=np.nan if i == 0 else 7.,
                         track_type=surfaces[i], dist_band="mile", venue_code=venues[i],
                         won=False, finish_order=2, result_status="finished",
                         prev_track_type="芝" if i else None, prev_venue_code="05" if i else None,
                         prev_running_style="差し" if i else None,
                         odds=9., prev_odds=3. if i else np.nan, running_style="後方", sentinel=f"B{i}"))
    frame = pd.DataFrame(rows).iloc[[6, 5, 2, 7, 0, 4, 1, 3]].copy()
    frame.index = [22, 22, 9, 11, 18, 7, 8, 19]
    return frame


def get(frame, horse, date):
    return frame[(frame.horse_id == horse) & (frame.race_date == date)].iloc[0]


def test_histories_exclude_current_and_do_not_merge_horse_ids():
    raw = sample()
    got = features.build_features(raw)
    pd.testing.assert_frame_equal(got[raw.columns], raw)
    first = get(got, "A", "2007-01-06")
    assert first.surface_starts == first.surface_wins == 0
    assert np.isnan(first.surface_top3_rate)
    assert np.isnan(first.prev_gap_days) and not first.rest_second
    back = get(got, "A", "2007-01-20")
    assert back.surface_starts == back.surface_wins == back.surface_top3_rate == 1
    assert back.distband_starts == back.distband_wins == 1
    assert back.course_starts == back.course_wins == 1
    assert bool(back.return_to_surface) and bool(back.return_to_course)
    assert back.prev_gap_days == 7 and np.isnan(back.prev2_gap_days)
    other = get(got, "B", "2007-01-13")
    assert other.surface_starts == other.surface_wins == 0
    # A's first win and B's first top-three finish must never cross identities.
    assert other.course_wins == 0


def test_own_race_and_future_outcome_changes_leave_prior_and_current_features_unchanged():
    raw = sample()
    baseline = features.build_features(raw)
    changed = raw.copy()
    mask = changed.race_id == "r2"
    changed.loc[mask, ["won", "finish_order", "result_status", "running_style"]] = [True, 1, "finished", "追込"]
    recomputed = features.build_features(changed)
    earlier = raw.race_date <= "2007-01-20"
    pd.testing.assert_frame_equal(baseline.loc[earlier, features.NEW_COLUMNS],
                                  recomputed.loc[earlier, features.NEW_COLUMNS])
    # Sanity: the changed outcome does reach this horse's later history.
    assert get(recomputed, "A", "2007-04-14").surface_wins == get(baseline, "A", "2007-04-14").surface_wins + 1
    future = raw.copy()
    future.loc[future.race_date == "2007-05-26", ["won", "finish_order"]] = [True, 1]
    pd.testing.assert_frame_equal(baseline.loc[:, features.NEW_COLUMNS],
                                  features.build_features(future).loc[:, features.NEW_COLUMNS])


def test_rest_calendar_gaps_and_pace_use_previous_styles_only():
    got = features.build_features(sample())
    second = get(got, "A", "2007-05-05")
    third = get(got, "A", "2007-05-26")
    assert second.prev_gap_days == 84 and second.rest_second and not second.rest_third
    assert third.prev2_gap_days == 84 and third.rest_third and not third.rest_second
    a = get(got, "A", "2007-01-13")
    b = get(got, "B", "2007-01-13")
    assert a.pace_front_count == b.pace_front_count == 1
    assert a.pace_front_share == b.pace_front_share == .5
    assert a.pace_front_others == 0 and b.pace_front_others == 1
    assert a.pace_known_count == b.pace_known_count == 2
    assert a.odds_ratio_prev == 2 and b.odds_ratio_prev == 3


def test_missing_keys_and_invalid_odds_remain_unknown():
    raw = sample().iloc[[0]].copy()
    raw.loc[:, ["track_type", "venue_code"]] = None
    raw.loc[:, "prev_odds"] = 0.
    got = features.build_features(raw).iloc[0]
    for prefix in ("surface", "distband", "course"):
        assert np.isnan(got[f"{prefix}_starts"])
        assert np.isnan(got[f"{prefix}_wins"])
        assert np.isnan(got[f"{prefix}_top3_rate"])
    assert not got.return_to_surface and not got.return_to_course
    assert np.isnan(got.odds_ratio_prev)
    assert got.pace_known_count == 0


def test_nonfinishers_count_as_starts_without_top3_or_win():
    raw = sample()
    mask = (raw.horse_id == "A") & (raw.race_date == "2007-01-06")
    raw.loc[mask, ["won", "finish_order", "result_status"]] = [False, np.nan, "stopped"]
    back = get(features.build_features(raw), "A", "2007-01-20")
    assert back.surface_starts == 1 and back.surface_wins == 0 and back.surface_top3_rate == 0


def test_rejects_pre2007_and_same_horse_same_day_rows():
    raw = sample()
    old = raw.copy()
    old.iloc[0, old.columns.get_loc("race_date")] = "2006-12-31"
    with pytest.raises(AssertionError, match="pre-2007"):
        features.build_features(old)
    with pytest.raises(AssertionError, match="multiple starts"):
        features.build_features(pd.concat([raw, raw.iloc[[0]]]))


def test_boundaries_are_inclusive_and_return_flag_is_only_a_change():
    raw = sample()
    # Test the requested inclusive predicates directly through supplied calendar-day gap columns.
    idx = raw.index
    raw = raw.reset_index(drop=True)
    raw.loc[raw.race_date == "2007-05-05", "days_since_last"] = 14.
    raw.loc[raw.race_date == "2007-05-26", "days_since_last"] = 56.
    got = features.build_features(raw)
    assert get(got, "A", "2007-05-05").rest_second
    assert get(got, "A", "2007-05-26").rest_third
    change = get(got, "A", "2007-01-13")
    assert change.return_to_surface and change.surface_starts == 0
