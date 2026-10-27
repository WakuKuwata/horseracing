"""Exact-key, missing-value and strictly-before-day checks of 143 additions."""
import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_143/features.py"
spec = importlib.util.spec_from_file_location("features143", SOURCE)
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


def fixture_frames():
    races, entries, results = [], [], []
    for i in range(7):
        rid = f"20190101{i + 1:04d}"
        # Two same-day races test exclusion of all current-day results.
        day = pd.Timestamp("2019-01-01") + pd.Timedelta(days=i if i < 6 else 5)
        races.append(dict(race_id=rid, race_date=day, venue_code="01", track_type="芝", distance=1600, going="良"))
        for h in range(3):
            hid = f"h{h}" if i != 6 else f"other{h}"
            entries.append(dict(race_id=rid, horse_id=hid, horse_number=h + 1,
                                entry_status="started", running_style=("逃げ", "差し", "先行")[h]))
            results.append(dict(race_id=rid, horse_id=hid, finish_order=h + 1, result_status="finished",
                                last_3f=34 + h + i * .1, finish_time=pd.Timedelta(seconds=95 + h + i * .2),
                                finish_time_diff=pd.Timedelta(seconds=h * .5), corner_orders=[h + 1, 3 - h]))
    frames = f.Frames(pd.DataFrame(races), pd.DataFrame(entries), pd.DataFrame(results),
                      pd.DataFrame({"horse_id": ["mutable"], "owner_name": ["must not be read"]}))
    return frames


def reference(frames, ids=None):
    d = frames.race_horses.merge(frames.races[["race_id", "race_date"]], on="race_id")
    if ids:
        d = d[d.race_id.isin(ids)]
    return d[["race_id", "horse_id", "horse_number", "race_date"]].reset_index(drop=True)


def test_feature_contract():
    assert len(f.SPEED_COLUMNS) == 10
    assert len(f.PACE_COLUMNS) == 14
    assert len(set(f.ADDED_COLUMNS)) == 24
    assert not set(f.ADDED_COLUMNS) & set(f.baseline.FEATURES)


def test_target_and_same_day_other_results_do_not_change_features(monkeypatch):
    monkeypatch.setattr(f.speed, "MIN_RACES", 2)
    frames = fixture_frames()
    rid = frames.races.race_id.iloc[5]
    ref = reference(frames, [rid])
    expected = f.build_additions(frames, ref)
    rr, rh = frames.race_results.copy(), frames.race_horses.copy()
    current_ids = frames.races.loc[frames.races.race_date >= ref.race_date.iloc[0], "race_id"]
    rr.loc[rr.race_id.isin(current_ids), "last_3f"] = 1e6
    rr.loc[rr.race_id.isin(current_ids), "finish_order"] = 999
    rr.loc[rr.race_id.isin(current_ids), "finish_time"] = pd.Timedelta(seconds=1e6)
    rr.loc[rr.race_id.isin(current_ids), "finish_time_diff"] = pd.Timedelta(seconds=1e6)
    for idx in rr.index[rr.race_id.isin(current_ids)]:
        rr.at[idx, "corner_orders"] = [999, 999]
    rh.loc[rh.race_id.isin(current_ids), "running_style"] = "追込"
    actual = f.build_additions(replace(frames, race_horses=rh, race_results=rr), ref)
    pd.testing.assert_frame_equal(expected, actual)
    assert expected[list(f.SPEED_COLUMNS)].notna().all().all()


def test_future_outside_bounds_cannot_change_features(monkeypatch):
    monkeypatch.setattr(f.speed, "MIN_RACES", 2)
    frames = fixture_frames()
    ref = reference(frames)
    expected = f.build_additions(frames, ref)
    extra = frames.races.iloc[[0]].copy()
    extra["race_date"] = pd.Timestamp("2026-06-27")
    # Deliberately duplicate ID becomes harmless only if date bounding occurs first.
    altered = replace(frames, races=pd.concat([frames.races, extra], ignore_index=True))
    pd.testing.assert_frame_equal(expected, f.build_additions(altered, ref))
    assert f.bounded_frames(altered).horses.empty


def test_order_retained_and_no_history_stays_nan():
    frames = fixture_frames()
    ref = reference(frames).sample(frac=1, random_state=3).reset_index(drop=True)
    added = f.build_additions(frames, ref)
    pd.testing.assert_frame_equal(added[f.KEYS], ref[f.KEYS])
    first = added.race_id.eq(frames.races.race_id.iloc[0])
    assert added.loc[first, "rel_time_avg"].isna().all()
    assert added.loc[first, "front_runner_rate"].isna().all()
    assert added.loc[first, "asof_spdfig_count"].eq(0).all()
    joined = f.join_additions(ref, added)
    assert len(joined) == len(ref)
    assert joined.loc[first, "rel_time_avg"].isna().all()


def test_exact_missing_alias_or_partial_field_fails():
    frames = fixture_frames()
    ref = reference(frames)
    bad = ref.copy()
    bad.loc[0, "horse_id"] = "nk:h0"
    with pytest.raises(ValueError, match="no alias matching"):
        f.build_additions(frames, bad)
    with pytest.raises(ValueError, match="complete started field"):
        f.build_additions(frames, ref.iloc[1:])
    wrong_number = ref.copy()
    wrong_number.loc[0, "horse_number"] = 9
    with pytest.raises(ValueError, match="horse_number differs"):
        f.build_additions(frames, wrong_number)


def test_duplicate_keys_and_unregistered_duplicate_numbers_fail():
    frames = fixture_frames()
    rh = pd.concat([frames.race_horses, frames.race_horses.iloc[[0]]])
    with pytest.raises(ValueError, match="duplicate exact"):
        f.bounded_frames(replace(frames, race_horses=rh))
    rh = frames.race_horses.copy()
    rh.loc[1, "horse_number"] = rh.horse_number.iloc[0]
    with pytest.raises(ValueError, match="unregistered duplicate"):
        f.bounded_frames(replace(frames, race_horses=rh))


def test_registered_anomalies_whole_race_excluded():
    frames = fixture_frames()
    rid = frames.races.race_id.iloc[0]
    target = next(iter(f.HISTORY_EXCLUDED_RACES))
    altered = replace(frames, **{name: getattr(frames, name).replace({"race_id": {rid: target}})
                                 for name in ("races", "race_horses", "race_results")})
    bounded = f.bounded_frames(altered)
    assert all(target not in set(getattr(bounded, name).race_id)
               for name in ("races", "race_horses", "race_results"))


def test_missing_addition_rows_fail_but_nan_values_remain():
    frames = fixture_frames()
    ref = reference(frames)
    added = f.build_additions(frames, ref)
    with pytest.raises(ValueError, match="key coverage"):
        f.join_additions(ref, added.iloc[1:])
    duplicate = pd.concat([added, added.iloc[[0]]])
    with pytest.raises(ValueError, match="duplicate exact"):
        f.join_additions(ref, duplicate)
    added[list(f.ADDED_COLUMNS)] = np.nan
    assert f.join_additions(ref, added)[list(f.ADDED_COLUMNS)].isna().all().all()


def test_prefix_truncation_matches_full_pool(monkeypatch):
    monkeypatch.setattr(f.speed, "MIN_RACES", 2)
    frames = fixture_frames()
    rid = frames.races.race_id.iloc[4]
    ref = reference(frames, [rid])
    ids = frames.races.loc[frames.races.race_date <= ref.race_date.iloc[0], "race_id"]
    truncated = replace(frames, **{name: getattr(frames, name).loc[getattr(frames, name).race_id.isin(ids)].copy()
                                   for name in ("races", "race_horses", "race_results")})
    pd.testing.assert_frame_equal(f.build_additions(frames, ref), f.build_additions(truncated, ref))


def test_canceled_entry_is_excluded_from_opponents_scenario():
    frames = fixture_frames()
    rid = frames.races.race_id.iloc[5]
    rh = frames.race_horses.copy()
    rh.loc[rh.race_id.eq(rid) & rh.horse_id.eq("h2"), "entry_status"] = "scratched"
    altered = replace(frames, race_horses=rh)
    ref = reference(altered, [rid])
    ref = ref[ref.horse_id.ne("h2")].reset_index(drop=True)
    actual = f.build_additions(altered, ref).set_index("horse_id")
    assert actual.loc["h0", "field_front_rate_ex_self"] == 0.
    assert actual.loc["h1", "field_front_rate_ex_self"] == 1.
    assert actual.field_style_coverage.eq(1).all()
    # Both runners would face a different front rate if canceled h2 were counted.
    assert len(actual) == 2
