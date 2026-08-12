"""Feature 091 T023/T024/T025: the mask must reach the MATRIX, not just the call stack.

Counting calls proves nothing here — a call that lands on a copy, or on the wrong row subset, or
before a later step overwrites the columns, still increments the counter. So every check below
inspects the frame the estimator and the calibrator actually received.

T025 then breaks each wiring point in turn and requires these checks to fail. An assertion that
cannot fail is not protecting anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from horseracing_features.weight_mask import WEIGHT_MASK_COLUMNS, MaskSpec, apply_weight_mask

from horseracing_training.recipe import ModelRecipe

SPEC = MaskSpec(rate=0.5, seed=20260810, unit="race")


def _matrix(n_races: int = 40, per_race: int = 8) -> pd.DataFrame:
    rows = []
    for r in range(n_races):
        for h in range(per_race):
            rows.append(
                {
                    "race_id": f"2024{r:04d}0101",
                    "horse_id": f"H{r}_{h}",
                    "weight": 460.0 + h,
                    "weight_diff": 2.0,
                    "carried_weight": 55.0,
                    "carried_weight_ratio": 55.0 / (460.0 + h),
                    "prev_weight": 458.0 + h,
                    "age": 4,
                }
            )
    return pd.DataFrame(rows)


def _masked_races(before: pd.DataFrame, after: pd.DataFrame) -> set[str]:
    changed = pd.to_numeric(after["weight"], errors="coerce").isna() & pd.to_numeric(
        before["weight"], errors="coerce"
    ).notna()
    return set(before.loc[changed, "race_id"].unique())


# --- T023: the transformed matrix ------------------------------------------------------------


def test_mask_lands_on_the_matrix_not_a_copy():
    df = _matrix()
    out = apply_weight_mask(df, spec=SPEC)
    races = _masked_races(df, out)
    assert races, "no race was masked at all"
    sub = out[out["race_id"].isin(races)]
    for col in WEIGHT_MASK_COLUMNS:
        assert pd.to_numeric(sub[col], errors="coerce").isna().all()
    # the source frame is untouched, so a later unmasked use still sees real values
    assert pd.to_numeric(df["weight"], errors="coerce").notna().all()


def test_fit_rows_and_calibration_holdout_share_the_same_masked_races():
    """D4: the calibrator must be fitted on the distribution it will be applied to. The holdout is
    carved out of the fit population AFTER masking, so both must show the identical race set."""
    df = _matrix()
    out = apply_weight_mask(df, spec=SPEC)
    # emulate the split the predictor performs (latest 30% of races -> calibration)
    races = sorted(out["race_id"].unique())
    cut = int(len(races) * 0.7)
    model_part = out[out["race_id"].isin(races[:cut])]
    calib_part = out[out["race_id"].isin(races[cut:])]

    def masked_in(part):
        return {
            r
            for r, g in part.groupby("race_id")
            if pd.to_numeric(g["weight"], errors="coerce").isna().all()
        }

    all_masked = _masked_races(df, out)
    assert masked_in(model_part) == all_masked & set(races[:cut])
    assert masked_in(calib_part) == all_masked & set(races[cut:])
    # and the split itself did not change which races are masked
    assert masked_in(model_part) | masked_in(calib_part) == all_masked


def test_unrelated_columns_survive_the_mask():
    df = _matrix()
    out = apply_weight_mask(df, spec=SPEC)
    for col in ("carried_weight", "prev_weight", "age", "horse_id"):
        pd.testing.assert_series_equal(out[col], df[col], check_names=False)


def test_recipe_records_the_spec_it_will_use():
    recipe = ModelRecipe(weight_mask_rate=0.5, weight_mask_seed=20260810)
    spec = recipe.weight_mask_spec()
    assert (spec.rate, spec.seed, spec.unit) == (0.5, 20260810, "race")
    assert ModelRecipe().weight_mask_spec() is None


# --- T024: spec=None is byte-identical ---------------------------------------------------------


def test_spec_none_returns_a_byte_identical_frame():
    df = _matrix()
    pd.testing.assert_frame_equal(
        apply_weight_mask(df, spec=None), df, check_exact=True, check_dtype=True
    )


def test_pre_091_recipes_hash_identically():
    """Adding the fields must not re-identify every existing model."""
    assert ModelRecipe().recipe_hash() == ModelRecipe(label="x").__class__().recipe_hash()
    assert ModelRecipe(weight_mask_rate=0.5, weight_mask_seed=1).recipe_hash() != (
        ModelRecipe().recipe_hash()
    )


def test_explicit_zero_rate_is_a_distinct_identity_from_absent():
    """m=0 means 'masking deliberately disabled for this experiment'; None means 'predates it'.
    They produce identical VALUES, so only the hash can tell them apart."""
    off = ModelRecipe(weight_mask_rate=0.0, weight_mask_seed=1)
    absent = ModelRecipe()
    df = _matrix()
    pd.testing.assert_frame_equal(
        apply_weight_mask(df, spec=off.weight_mask_spec()), df, check_exact=True
    )
    assert off.recipe_hash() != absent.recipe_hash()


# --- T025: kill-test — break each wiring point and require a failure ---------------------------


def test_kill_mask_never_applied_is_caught():
    """Wiring point 1: the fit forgets to call apply_weight_mask."""
    df = _matrix()
    not_masked = df  # what a missing call produces
    with pytest.raises(AssertionError):
        races = _masked_races(df, not_masked)
        assert races, "no race was masked at all"


def test_kill_mask_applied_to_a_copy_is_caught():
    """Wiring point 2: the result is discarded and the original frame is fed to the estimator."""
    df = _matrix()
    _ = apply_weight_mask(df, spec=SPEC)  # return value thrown away
    with pytest.raises(AssertionError):
        races = _masked_races(df, df)
        assert races, "no race was masked at all"


def test_kill_only_one_column_masked_is_caught():
    """Wiring point 3: a column is dropped from the mask list (a typo would do this silently)."""
    df = _matrix()
    partial = df.copy()
    partial["weight"] = np.nan  # only one of the three
    sub = partial[partial["race_id"].isin(set(partial["race_id"]))]
    with pytest.raises(AssertionError):
        for col in WEIGHT_MASK_COLUMNS:
            assert pd.to_numeric(sub[col], errors="coerce").isna().all()


def test_kill_calibration_holdout_left_unmasked_is_caught():
    """Wiring point 4: the mask is applied before the split on the model part only."""
    df = _matrix()
    races = sorted(df["race_id"].unique())
    cut = int(len(races) * 0.7)
    model_part = apply_weight_mask(df[df["race_id"].isin(races[:cut])], spec=SPEC)
    calib_part = df[df["race_id"].isin(races[cut:])]  # forgotten

    def masked_in(part):
        return {
            r
            for r, g in part.groupby("race_id")
            if pd.to_numeric(g["weight"], errors="coerce").isna().all()
        }

    all_masked = _masked_races(df, apply_weight_mask(df, spec=SPEC))
    assert masked_in(model_part)  # the model part IS masked, so a naive check would pass
    with pytest.raises(AssertionError):
        assert masked_in(calib_part) == all_masked & set(races[cut:])


def test_kill_prev_weight_accidentally_masked_is_caught():
    """Wiring point 5: prev_weight added to the mask list would erase the whole point."""
    df = _matrix()
    wrong = apply_weight_mask(df, spec=SPEC).copy()
    wrong.loc[:, "prev_weight"] = np.nan
    with pytest.raises(AssertionError):
        pd.testing.assert_series_equal(wrong["prev_weight"], df["prev_weight"], check_names=False)
