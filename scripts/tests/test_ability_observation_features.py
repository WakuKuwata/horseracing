"""Temporal and semantic checks for the research-only observation candidates."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ability_observation_features as obs
import horseracing_features.speed_figure_features as sf
from horseracing_db.enums import ResultStatus
from horseracing_features.loader import Frames


def frames_of(rows):
    """Rows: (race ID, date, horse ID, finish seconds), one horse per race."""
    races = pd.DataFrame([{"race_id": r, "race_date": pd.Timestamp(d), "venue_code": "05",
                           "track_type": "TURF", "distance": 1600, "going": "GOOD"}
                          for r, d, h, t in rows]).drop_duplicates("race_id")
    rh = pd.DataFrame([{"race_id": r, "horse_id": h} for r, d, h, t in rows])
    rr = rh.copy()
    rr["finish_time"] = [t for r, d, h, t in rows]
    rr["result_status"] = ResultStatus.FINISHED
    return Frames(races, rh, rr)


def specs():
    return [("b1", "2008-01-01", "A", 100.), ("b2", "2008-01-01", "B", 102.),
            ("p1", "2008-03-01", "H", 95.), ("p2", "2008-04-01", "H", 101.),
            ("target", "2008-06-01", "H", 96.), ("new", "2008-06-01", "N", 99.)]


@pytest.fixture(autouse=True)
def small_baseline(monkeypatch):
    monkeypatch.setattr(sf, "MIN_RACES", 2)


def target(frames, race="target"):
    return obs.build_observation_features(frames).set_index(["race_id", "horse_id"]).loc[race]


def test_values_match_hand_calculated_past_figures():
    out = target(frames_of(specs())).loc["H"]
    # First figure clips to 5. Second baseline samples are 100, 102, 95.
    z2 = (np.mean([100., 102., 95.]) - 101.) / np.std([100., 102., 95.], ddof=0)
    assert out.asof_spdfig_last_age_days == 61.
    assert out.asof_spdfig_best_age_days == 92.
    assert out.asof_spdfig_sd == pytest.approx(np.std([5., z2], ddof=0))


def test_ties_use_latest_attainment_and_constant_observations_have_zero_sd():
    rows = specs()
    rows[3] = ("p2", "2008-04-01", "H", 50.)  # also clipped to 5
    out = target(frames_of(rows)).loc["H"]
    assert out.asof_spdfig_last_age_days == out.asof_spdfig_best_age_days == 61.
    assert out.asof_spdfig_sd == 0.


def test_missing_history_and_only_one_valid_observation_remain_nan():
    frames = frames_of(specs())
    assert target(frames, "new").loc["N"].isna().all()
    one = target(frames, "p2").loc["H"]
    assert one.asof_spdfig_last_age_days == one.asof_spdfig_best_age_days == 31.
    assert pd.isna(one.asof_spdfig_sd)


def test_invalid_recent_result_does_not_reset_last_valid_age():
    rows = specs()
    rows[3] = ("p2", "2008-04-01", "H", None)
    out = target(frames_of(rows)).loc["H"]
    assert out.asof_spdfig_last_age_days == out.asof_spdfig_best_age_days == 92.
    assert pd.isna(out.asof_spdfig_sd)


@pytest.mark.parametrize("mutation", ["current", "same_day_other", "future"])
def test_target_results_same_day_other_horse_and_future_do_not_change_features(mutation):
    rows = specs()
    expected = target(frames_of(rows))
    if mutation == "current":
        rows[4] = ("target", "2008-06-01", "H", 20.)
    elif mutation == "same_day_other":
        rows[5] = ("new", "2008-06-01", "N", 20.)
    else:
        rows += [("future", "2009-01-01", "H", 20.)]
    pd.testing.assert_frame_equal(expected, target(frames_of(rows)), check_exact=True)


def test_cross_horse_same_day_baseline_is_excluded_but_earlier_baseline_matters():
    # Use the second observation's day as the boundary: same-day other-race
    # changes must not enter that figure. A later target sees two observations.
    rows = specs()
    rows[3] = ("p2", "2008-04-01", "H", 101.)
    rows += [("other", "2008-04-01", "C", 99.)]
    expected = target(frames_of(rows))
    rows[-1] = ("other", "2008-04-01", "C", 20.)
    same_day_changed = target(frames_of(rows))
    pd.testing.assert_frame_equal(expected.iloc[:, :2], same_day_changed.iloc[:, :2], check_exact=True)
    # Inherited baseline uses cumsum minus the current day's sum. Cancellation
    # can change the SD by ~3e-14; only this continuous result uses tolerance.
    # Missingness, IDs, age values and all other invariance checks stay exact.
    pd.testing.assert_series_equal(expected.asof_spdfig_sd.isna(), same_day_changed.asof_spdfig_sd.isna())
    np.testing.assert_allclose(expected.asof_spdfig_sd, same_day_changed.asof_spdfig_sd,
                               atol=1e-12, rtol=0, equal_nan=True)
    rows[0] = ("b1", "2008-01-01", "A", 90.)
    changed = target(frames_of(rows))
    assert expected.loc["H", "asof_spdfig_sd"] != changed.loc["H", "asof_spdfig_sd"]


def test_target_subset_retains_full_cross_horse_baseline_and_is_pool_end_independent():
    frames = frames_of(specs())
    expected = target(frames)
    subset = replace(frames, race_horses=frames.race_horses.query("race_id == 'target'"))
    pd.testing.assert_frame_equal(expected, target(subset), check_exact=True)
    extended = frames_of(specs() + [("future", "2009-01-01", "H", 20.)])
    pd.testing.assert_frame_equal(expected, target(extended), check_exact=True)


def test_shuffled_input_is_key_invariant_preserves_target_order_and_does_not_mutate():
    frames = frames_of(specs())
    shuffled = replace(frames, races=frames.races.sample(frac=1, random_state=1),
                       race_horses=frames.race_horses.sample(frac=1, random_state=2),
                       race_results=frames.race_results.sample(frac=1, random_state=3))
    originals = [x.copy(deep=True) for x in [shuffled.races, shuffled.race_horses, shuffled.race_results]]
    actual = obs.build_observation_features(shuffled)
    pd.testing.assert_frame_equal(actual[["race_id", "horse_id"]],
                                  shuffled.race_horses.reset_index(drop=True), check_exact=True)
    pd.testing.assert_frame_equal(actual.sort_values(["race_id", "horse_id"]).reset_index(drop=True),
        obs.build_observation_features(frames).sort_values(["race_id", "horse_id"]).reset_index(drop=True),
        check_exact=True)
    for original, source in zip(originals, [shuffled.races, shuffled.race_horses, shuffled.race_results]):
        pd.testing.assert_frame_equal(original, source, check_exact=True)


@pytest.mark.parametrize("result_only", [False, True])
def test_ambiguous_horse_day_rejected_before_valid_figure_filter(result_only):
    frames = frames_of(specs() + [("bad", "2008-04-01", "H", None)])
    if result_only:
        frames = replace(frames, race_horses=frames.race_horses.query("race_id != 'bad'"))
    with pytest.raises(ValueError, match="same-horse/same-day"):
        obs.build_observation_features(frames)


def test_intraday_timestamps_are_normalized_to_whole_day_boundary():
    frames = frames_of(specs())
    intraday = replace(frames, races=frames.races.assign(
        race_date=frames.races.race_date + pd.Timedelta(hours=12)))
    pd.testing.assert_frame_equal(obs.build_observation_features(frames),
                                  obs.build_observation_features(intraday), check_exact=True)


def test_empty_targets_and_no_valid_figure_pool_return_typed_nan_schema():
    frames = frames_of(specs()[:2])
    out = obs.build_observation_features(frames)
    assert out[obs.OBSERVATION_COLUMNS].isna().all().all()
    assert all(out[c].dtype == np.dtype("float64") for c in obs.OBSERVATION_COLUMNS)
    empty = obs.build_observation_features(replace(frames, race_horses=frames.race_horses.iloc[:0]))
    assert empty.empty and list(empty.columns) == ["race_id", "horse_id", *obs.OBSERVATION_COLUMNS]
