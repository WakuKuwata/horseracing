"""Feature 091 T027: race-level weight availability normalisation (INV-W11 / INV-W12).

Training masks the same-day weight columns race-atomically, so the serving input must be the same
binary: a race is either fully weighed or treated as fully unweighed. A partially weighed race fed
to the model would be out-of-distribution, and because the objective is a within-race softmax the
damage is not confined to the unweighed horses.

Checked here on the pure helper. The counts it returns are what T067 persists (FR-035); this test
only asserts they are produced, not that they are stored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from horseracing_serving.predictor import (
    PREV_WEIGHT_COLUMN,
    SAME_DAY_WEIGHT_COLUMNS,
    normalise_weight_availability,
)

WITH_PREV = ["weight", "weight_diff", "carried_weight_ratio", "prev_weight", "age"]
WITHOUT_PREV = ["weight", "weight_diff", "carried_weight_ratio", "age"]


def _race(weights: list[float | None]) -> pd.DataFrame:
    n = len(weights)
    return pd.DataFrame(
        {
            "weight": weights,
            "weight_diff": [2.0] * n,
            "carried_weight_ratio": [0.12] * n,
            "prev_weight": [460.0 + i for i in range(n)],
            "age": [4] * n,
        },
        index=[f"H{i}" for i in range(n)],
    )


def test_fully_weighed_race_is_untouched():
    rows = _race([480.0, 490.0, 500.0])
    got = normalise_weight_availability(rows, feature_cols=WITH_PREV)
    assert got.applicable and not got.normalised
    assert (got.n_started, got.n_weighed) == (3, 3)
    pd.testing.assert_frame_equal(got.rows, rows, check_exact=True)


def test_fully_unweighed_race_is_untouched():
    """Already uniform — nothing to collapse, and the frame must not be needlessly rewritten."""
    rows = _race([None, None, None])
    got = normalise_weight_availability(rows, feature_cols=WITH_PREV)
    assert got.applicable and not got.normalised
    assert (got.n_started, got.n_weighed) == (3, 0)
    pd.testing.assert_frame_equal(got.rows, rows, check_exact=True)


def test_partial_race_drops_same_day_weight_for_every_horse():
    """INV-W11: the three weighed horses lose their weight too, so no mixed input reaches the model."""
    rows = _race([480.0, None, 500.0, None])
    got = normalise_weight_availability(rows, feature_cols=WITH_PREV)
    assert got.applicable and got.normalised
    assert (got.n_started, got.n_weighed) == (4, 2)
    for col in SAME_DAY_WEIGHT_COLUMNS:
        assert got.rows[col].isna().all(), f"{col} still has a value for some horse"


def test_partial_race_keeps_prev_weight_and_unrelated_columns():
    rows = _race([480.0, None, 500.0])
    got = normalise_weight_availability(rows, feature_cols=WITH_PREV)
    pd.testing.assert_series_equal(got.rows[PREV_WEIGHT_COLUMN], rows[PREV_WEIGHT_COLUMN])
    pd.testing.assert_series_equal(got.rows["age"], rows["age"])


def test_model_without_prev_weight_is_never_normalised():
    """INV-W12 / SC-004: the current active model has no fallback, so it keeps what it was given."""
    rows = _race([480.0, None, 500.0])
    got = normalise_weight_availability(rows, feature_cols=WITHOUT_PREV)
    assert not got.applicable and not got.normalised
    pd.testing.assert_frame_equal(got.rows, rows, check_exact=True)
    # the weighed horses keep their real weight — nothing was taken away
    assert got.rows["weight"].tolist()[0] == 480.0


def test_input_frame_is_not_mutated():
    rows = _race([480.0, None, 500.0])
    before = rows.copy(deep=True)
    normalise_weight_availability(rows, feature_cols=WITH_PREV)
    pd.testing.assert_frame_equal(rows, before, check_exact=True)


def test_counts_are_reported_for_observability():
    """FR-035: how often full-info is given up must be observable."""
    got = normalise_weight_availability(_race([480.0, None, None, 500.0, 490.0]), feature_cols=WITH_PREV)
    assert (got.n_started, got.n_weighed, got.normalised) == (5, 3, True)


def test_same_day_column_list_matches_the_features_layer():
    """The serving copy of the column list must not drift from the mask contract."""
    from horseracing_features.weight_mask import WEIGHT_MASK_COLUMNS

    assert tuple(SAME_DAY_WEIGHT_COLUMNS) == tuple(WEIGHT_MASK_COLUMNS)


@pytest.mark.parametrize("missing_col", ["weight_diff", "carried_weight_ratio"])
def test_absent_derived_columns_are_tolerated_when_normalising(missing_col):
    """A model may not carry every derived same-day column; the ones present must still clear."""
    rows = _race([480.0, None]).drop(columns=[missing_col])
    got = normalise_weight_availability(rows, feature_cols=WITH_PREV)
    assert got.normalised
    for col in SAME_DAY_WEIGHT_COLUMNS:
        if col in got.rows.columns:
            assert got.rows[col].isna().all()
    assert not np.isnan(got.rows[PREV_WEIGHT_COLUMN]).any()


def test_no_same_day_weight_column_at_all_is_a_no_op():
    """`weight` is the detector. Without it availability is uniformly absent by construction, so
    there is nothing to collapse — and nothing to report as full-info given up."""
    rows = _race([480.0, None]).drop(columns=["weight"])
    got = normalise_weight_availability(rows, feature_cols=WITH_PREV)
    assert got.applicable and not got.normalised
    assert (got.n_started, got.n_weighed) == (2, 0)
    pd.testing.assert_frame_equal(got.rows, rows, check_exact=True)
