from __future__ import annotations

import datetime

import pandas as pd
import pytest

from horseracing_training.dataset import TrainingMatrix
from horseracing_training.spelling_split import assert_arm_identity, make_arms


def _matrix() -> TrainingMatrix:
    frame = pd.DataFrame(
        {
            "race_id": ["r0", "r1", "r2", "r3"],
            "horse_id": ["h0", "h1", "h2", "h3"],
            "race_class": pd.Categorical(
                ["1勝", "1勝", "2勝", "オープン"],
                categories=["1勝", "2勝", "3勝", "オープン"],
            ),
            "speed": [10.0, 11.0, 12.0, 13.0],
            "race_date": [
                datetime.date(2019, 12, 31),
                datetime.date(2020, 1, 1),
                datetime.date(2020, 1, 2),
                datetime.date(2020, 1, 3),
            ],
            "win": [0, 1, 0, 1],
            "finish_rank": [2, 1, 3, 1],
            "mkt_odds": [2.0, 3.0, 4.0, 5.0],
        }
    )
    return TrainingMatrix(
        frame=frame,
        feature_cols=["race_class", "speed"],
        categorical_cols=["race_class"],
    )


def _allowed(matrix: TrainingMatrix, cutoff: datetime.date) -> pd.Series:
    return (matrix.frame["race_date"] >= cutoff) & matrix.frame["race_class"].isin(
        {"1勝", "2勝", "3勝"}
    )


def test_make_arms_and_assert_arm_identity_happy_path():
    matrix = _matrix()
    cutoff = datetime.date(2020, 1, 1)

    arm_a, arm_b = make_arms(matrix, mode="pseudo_split", cutoff=cutoff)
    audit = assert_arm_identity(arm_a, arm_b, allowed_rows_mask=_allowed(matrix, cutoff))

    assert arm_b is matrix
    assert arm_a.frame is not arm_b.frame
    # the copy keeps the source dtype (category) so LightGBM can fit it; values are compared as str
    assert isinstance(arm_a.frame["race_class"].dtype, pd.CategoricalDtype)
    assert isinstance(arm_b.frame["race_class"].dtype, pd.CategoricalDtype)
    assert arm_a.frame["race_class"].tolist() == ["1勝", "１勝", "２勝", "オープン"]
    assert audit["n_rows_differing"] == 2
    assert audit["n_rows"] == 4
    assert audit["race_class_hash_A"] != audit["race_class_hash_B"]


def test_canonicalise_arm_keeps_original_and_maps_a_copy():
    matrix = _matrix()
    matrix.frame["race_class"] = pd.Categorical(
        ["１勝", "２勝", "3勝", "オープン"],
        categories=["１勝", "２勝", "3勝", "オープン"],
    )

    arm_a, arm_b = make_arms(matrix, mode="canonicalise", cutoff=None)

    assert arm_a is matrix
    assert arm_b.frame is not arm_a.frame
    assert isinstance(arm_a.frame["race_class"].dtype, pd.CategoricalDtype)
    assert isinstance(arm_b.frame["race_class"].dtype, pd.CategoricalDtype)
    assert arm_b.frame["race_class"].tolist() == ["1勝", "2勝", "3勝", "オープン"]


def test_identical_arms_fail():
    matrix = _matrix()
    copied = TrainingMatrix(
        frame=matrix.frame.copy(),
        feature_cols=list(matrix.feature_cols),
        categorical_cols=list(matrix.categorical_cols),
    )

    with pytest.raises(AssertionError, match="no differing race_class rows"):
        assert_arm_identity(
            matrix,
            copied,
            allowed_rows_mask=pd.Series(True, index=matrix.frame.index),
        )


def test_extra_difference_outside_race_class_fails():
    matrix = _matrix()
    cutoff = datetime.date(2020, 1, 1)
    arm_a, arm_b = make_arms(matrix, mode="pseudo_split", cutoff=cutoff)
    arm_a.frame.loc[0, "speed"] = 999.0

    with pytest.raises(AssertionError, match="columns other than race_class"):
        assert_arm_identity(arm_a, arm_b, allowed_rows_mask=_allowed(matrix, cutoff))


def test_difference_on_non_allowed_row_fails():
    matrix = _matrix()
    cutoff = datetime.date(2020, 1, 1)
    arm_a, arm_b = make_arms(matrix, mode="pseudo_split", cutoff=cutoff)
    arm_a.frame.loc[0, "race_class"] = "１勝"

    with pytest.raises(AssertionError, match="outside allowed_rows_mask"):
        assert_arm_identity(arm_a, arm_b, allowed_rows_mask=_allowed(matrix, cutoff))


def test_projection_hash_is_row_order_independent_and_value_sensitive():
    matrix = _matrix()
    cutoff = datetime.date(2020, 1, 1)
    allowed = _allowed(matrix, cutoff)
    arm_a, arm_b = make_arms(matrix, mode="pseudo_split", cutoff=cutoff)
    initial = assert_arm_identity(arm_a, arm_b, allowed_rows_mask=allowed)

    reordered_a = TrainingMatrix(
        frame=arm_a.frame.iloc[::-1].reset_index(drop=True),
        feature_cols=list(arm_a.feature_cols),
        categorical_cols=list(arm_a.categorical_cols),
    )
    reordered_b = TrainingMatrix(
        frame=arm_b.frame.iloc[::-1].reset_index(drop=True),
        feature_cols=list(arm_b.feature_cols),
        categorical_cols=list(arm_b.categorical_cols),
    )
    reordered = assert_arm_identity(
        reordered_a,
        reordered_b,
        allowed_rows_mask=allowed.iloc[::-1].reset_index(drop=True),
    )

    assert reordered["race_class_hash_A"] == initial["race_class_hash_A"]
    assert reordered["race_class_hash_B"] == initial["race_class_hash_B"]

    changed_b = TrainingMatrix(
        frame=arm_b.frame.copy(),
        feature_cols=list(arm_b.feature_cols),
        categorical_cols=list(arm_b.categorical_cols),
    )
    changed_b.frame["race_class"] = changed_b.frame["race_class"].astype(object)
    changed_b.frame.loc[1, "race_class"] = "3勝"
    changed = assert_arm_identity(arm_a, changed_b, allowed_rows_mask=allowed)

    assert changed["race_class_hash_A"] == initial["race_class_hash_A"]
    assert changed["race_class_hash_B"] != initial["race_class_hash_B"]
