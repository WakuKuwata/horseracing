"""Feature 073 US2: calibration_split_unit recipe-ization + back-compat hash + freeze guards.

Covers T016 (recipe_hash back-compat canonicalization), T017 (split-change save guard), and the
legacy freeze record (T021). No DB / no training run — pure logic.
"""

from __future__ import annotations

import pytest

from horseracing_training.artifacts import assert_split_unit_compatible
from horseracing_training.calibration import (
    CALIBRATION_SPLIT_UNITS,
    LEGACY_CALIBRATION_SPLIT_UNIT,
    select_split_fn,
    split_train_by_day,
    split_train_by_time,
)
from horseracing_training.legacy_freeze import build_freeze_record, write_freeze_record
from horseracing_training.recipe import ModelRecipe

# --- T016: recipe_hash back-compat canonicalization ---------------------------------------

def test_default_split_unit_is_legacy():
    assert ModelRecipe().calibration_split_unit == LEGACY_CALIBRATION_SPLIT_UNIT == "race_count_v1"


def test_legacy_split_omitted_from_hash_so_existing_recipes_are_unchanged():
    # A recipe that explicitly sets the legacy split hashes identically to one that omits it:
    # the pre-073 recipe_hash (no such field) is byte-preserved (SC-006).
    assert ModelRecipe().recipe_hash() == ModelRecipe(
        calibration_split_unit="race_count_v1"
    ).recipe_hash()


def test_day_split_changes_hash():
    assert ModelRecipe().recipe_hash() != ModelRecipe(
        calibration_split_unit="race_day_v1"
    ).recipe_hash()


def test_meta_keeps_split_unit_for_audit():
    # meta() (the audit view) retains the field even when it is the legacy default.
    assert ModelRecipe().meta()["calibration_split_unit"] == "race_count_v1"
    assert (
        ModelRecipe(calibration_split_unit="race_day_v1").meta()["calibration_split_unit"]
        == "race_day_v1"
    )


def test_unknown_split_unit_rejected_at_construction():
    with pytest.raises(ValueError):
        ModelRecipe(calibration_split_unit="bogus_v9")


def test_select_split_fn_maps_and_fails_closed():
    assert select_split_fn("race_count_v1") is split_train_by_time
    assert select_split_fn("race_day_v1") is split_train_by_day
    assert set(CALIBRATION_SPLIT_UNITS) == {"race_count_v1", "race_day_v1"}
    with pytest.raises(ValueError):
        select_split_fn("nope")


# --- T017: split-change save guard --------------------------------------------------------

def test_split_guard_allows_first_save_and_same_split():
    assert_split_unit_compatible(None, "race_count_v1", model_version="m")  # pre-073 row == legacy
    assert_split_unit_compatible("race_count_v1", "race_count_v1", model_version="m")
    assert_split_unit_compatible(None, None, model_version="m")


def test_split_guard_rejects_changed_split_under_same_version():
    with pytest.raises(ValueError, match="split change must use a new model_version"):
        assert_split_unit_compatible("race_count_v1", "race_day_v1", model_version="lgbm-063")


# --- T021: legacy freeze record -----------------------------------------------------------

def test_freeze_record_pins_digests_and_is_append_only(tmp_path):
    d = tmp_path / "lgbm-x"
    d.mkdir()
    (d / "model.txt").write_text("MODEL")
    (d / "calibrator.pkl").write_bytes(b"CALIB")
    (d / "preprocessor.pkl").write_bytes(b"PREP")

    rec = build_freeze_record(d, model_version="lgbm-x", frozen_at="2026-07-15")
    assert rec["calibration_split_unit"] == "race_count_v1"
    assert set(rec["artifact_digests"]) == {"model.txt", "calibrator.pkl", "preprocessor.pkl"}

    p = write_freeze_record(d, rec)
    assert p.exists()
    # idempotent: identical content re-write is a no-op
    write_freeze_record(d, rec)
    # append-only: differing content fails closed
    rec2 = dict(rec, frozen_at="2099-01-01")
    with pytest.raises(ValueError, match="append-only"):
        write_freeze_record(d, rec2)
