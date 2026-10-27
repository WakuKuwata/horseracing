from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "features/tests"))
from _frames import make_frames
import pace_style_unknown_features as f
from horseracing_training.dataset import TrainingMatrix


def fixtures(styles):
    return make_frames([
        {"race_id": f"20240501{i:02d}01", "race_date": f"2024-{i:02d}-01",
         "horses": [{"horse_id": "A", "running_style": style},
                    {"horse_id": "B", "running_style": style, "finish_order": 2}]}
        for i, style in enumerate(styles, 1)
    ])


def last(df):
    return df.sort_values(f.KEYS).tail(2).set_index("horse_id")


def test_unknown_history_is_nan_and_field_known_fraction_is_zero():
    new, old, diag = f.build_overlay(fixtures([None, None]))
    assert last(old).front_runner_rate.eq(0).all()
    assert last(old).field_style_coverage.eq(1).all()
    assert last(new).front_runner_rate.isna().all()
    assert last(new).closer_rate.isna().all()
    assert last(new).field_style_coverage.eq(0).all()
    assert last(diag).known_style_count5.eq(0).all()


def test_all_known_and_legacy_rear_nonmatch_are_exactly_unchanged():
    new, old, _ = f.build_overlay(fixtures(["先行", "後方", "中団", "追込", "ﾏｸﾘ"]))
    pd.testing.assert_frame_equal(new, old, check_exact=True)
    rear, _, _ = f.build_overlay(fixtures(["後方", "後方"]))
    assert last(rear).closer_rate.eq(0).all()


def test_unknown_stays_in_five_started_window_instead_of_falling_back():
    new, _, diag = f.build_overlay(fixtures(["逃げ", None, None, None, None, None, None]))
    assert last(new).front_runner_rate.isna().all()
    assert last(diag).started_count5.eq(5).all()
    assert last(diag).known_style_count5.eq(0).all()


def test_partial_unknown_uses_only_known_denominator():
    new, old, diag = f.build_overlay(fixtures(["先行", None, "差し", None]))
    assert last(new).front_runner_rate.eq(.5).all()
    assert last(old).front_runner_rate.eq(1 / 3).all()
    assert last(diag).known_style_count5.eq(2).all()


@pytest.mark.parametrize("which", ["target", "same_day", "future"])
def test_current_same_day_and_future_styles_cannot_change_target(which):
    frames = fixtures([None, "先行", "差し", "逃げ"])
    target_id = "202405010301"
    expected = f.build_overlay(frames)[0].query("race_id == @target_id")
    rh = frames.race_horses.copy()
    races = frames.races.copy()
    if which == "target":
        rh.loc[rh.race_id.eq(target_id), "running_style"] = "中団"
    elif which == "future":
        rh.loc[rh.race_id.eq("202405010401"), "running_style"] = None
    else:
        # Another appearance on the target date is excluded even with a known style.
        races.loc[races.race_id.eq("202405010401"), "race_date"] = pd.Timestamp("2024-03-01").date()
    actual = f.build_overlay(replace(frames, races=races, race_horses=rh))[0].query("race_id == @target_id")
    pd.testing.assert_frame_equal(expected, actual, check_exact=True)


def test_cancelled_appearance_does_not_consume_started_window():
    frames = fixtures(["逃げ", None, None])
    rh = frames.race_horses.copy()
    rh.loc[rh.race_id.eq("202405010201"), "entry_status"] = "cancelled"
    new, _, diag = f.build_overlay(replace(frames, race_horses=rh))
    assert last(new).front_runner_rate.eq(1).all()
    assert last(diag).started_count5.eq(1).all()


def test_unknown_nonempty_token_rejected_and_whitespace_is_unknown():
    with pytest.raises(ValueError, match="Unregistered"):
        f.known_mask(pd.Series(["未登録脚質"]))
    assert f.known_mask(pd.Series([None, np.nan, "", "  ", "後方"])).tolist() == [False] * 4 + [True]


def test_forbidden_dates_and_orphan_source_rows_rejected():
    frames = fixtures([None, None])
    races = frames.races.copy()
    # Synthetic guard input, not a read of post-cutoff real races.
    races.loc[0, "race_date"] = pd.Timestamp("2026-09-07").date()
    with pytest.raises(ValueError, match="forbidden"):
        f.build_overlay(replace(frames, races=races))
    rh = frames.race_horses.copy()
    rh.loc[0, "race_id"] = "unknown"
    with pytest.raises(ValueError, match="outside"):
        f.build_overlay(replace(frames, race_horses=rh))


def test_overlay_preserves_nonpermitted_columns_labels_dtypes_and_source():
    frames = fixtures([None, "先行", None])
    new, old, _ = f.build_overlay(frames)
    raw = old.copy()
    raw["win"] = [1, 0] * 3
    raw["unrelated"] = pd.Categorical(["x", "y"] * 3)
    matrix = TrainingMatrix(raw, f.OVERLAY_COLUMNS + ["unrelated"], ["unrelated"], {"original": True})
    before = raw.copy(deep=True)
    corrected = f.apply_overlay(matrix, new)
    pd.testing.assert_frame_equal(matrix.frame, before, check_exact=True)
    pd.testing.assert_frame_equal(corrected.frame[["win", "unrelated"]], before[["win", "unrelated"]], check_exact=True)
    assert corrected.feature_cols == matrix.feature_cols
    assert corrected.frame.dtypes.equals(matrix.frame.dtypes)
    assert corrected.build_audit["research_overlay_semantics"] == f.SEMANTICS_ID
    with pytest.raises(ValueError, match="missing"):
        f.apply_overlay(matrix, new.iloc[:-1])


def test_coverage_separates_unknown_zero_and_changes_without_double_counting():
    new, old, diag = f.build_overlay(fixtures([None, None]))
    report = f.coverage_report(old, new, diag)
    assert report["all_started"]["changed_rows"] == 2
    assert report["all_started"]["changed_races"] == 1
    assert report["all_started"]["prior_all_unknown_rows"] == 2
    assert report["all_started"]["columns"]["front_runner_rate"]["zero_to_unknown"] == 2
