"""Feature 098: build and verify spelling-only training-matrix arms."""

from __future__ import annotations

import datetime
from typing import Literal

import pandas as pd
from horseracing_eval.provenance import frame_projection_hash
from horseracing_features.race_class_canon import canonicalise, pseudo_split

from .dataset import TrainingMatrix

_RACE_CLASS = "race_class"
_HASH_COLUMNS = ("race_id", "horse_id", _RACE_CLASS)


def _transformed_copy(matrix: TrainingMatrix) -> tuple[TrainingMatrix, pd.DataFrame]:
    frame = matrix.frame.copy()
    # Mapping a category to a new spelling can silently discard its category or raise. The
    # evaluation consumer re-coerces model inputs to category after the spelling transform.
    frame[_RACE_CLASS] = frame[_RACE_CLASS].astype(object)
    copied = TrainingMatrix(
        frame=frame,
        feature_cols=list(matrix.feature_cols),
        categorical_cols=list(matrix.categorical_cols),
    )
    return copied, frame


def _recoerce_like(frame: pd.DataFrame, original: pd.DataFrame) -> None:
    """Give the transformed copy the same dtype the original carries for race_class.

    The transform runs on object values; if the source matrix already had the pandas category
    dtype (as every TrainingMatrix does), the copy must get it back or LightGBM rejects the
    object column at fit time. The transforms never introduce NaN, and that is asserted here so a
    spelling that vanished into a category gap cannot pass silently (INV-R7 analogue)."""
    if not isinstance(original[_RACE_CLASS].dtype, pd.CategoricalDtype):
        return
    before = int(frame[_RACE_CLASS].isna().sum())
    frame[_RACE_CLASS] = frame[_RACE_CLASS].astype("category")
    after = int(frame[_RACE_CLASS].isna().sum())
    if after != before:
        raise AssertionError(f"race_class re-coercion changed NaN count {before} -> {after}")


def make_arms(
    matrix: TrainingMatrix,
    *,
    mode: Literal["pseudo_split", "canonicalise"],
    cutoff: datetime.date | None,
) -> tuple[TrainingMatrix, TrainingMatrix]:
    """Return arms ``(A, B)`` that differ only in ``race_class`` spelling.

    ``pseudo_split`` keeps the original canonical matrix as B and transforms copied arm A.
    ``canonicalise`` keeps the original raw matrix as A and transforms copied arm B.
    """
    if mode == "pseudo_split":
        if cutoff is None:
            raise ValueError("cutoff is required for mode='pseudo_split'")
        arm_a, frame = _transformed_copy(matrix)
        frame[_RACE_CLASS] = pseudo_split(frame[_RACE_CLASS], frame["race_date"], cutoff)
        _recoerce_like(frame, matrix.frame)
        return arm_a, matrix
    if mode == "canonicalise":
        arm_b, frame = _transformed_copy(matrix)
        frame[_RACE_CLASS], _audit = canonicalise(frame[_RACE_CLASS])
        _recoerce_like(frame, matrix.frame)
        return matrix, arm_b
    raise ValueError(f"unsupported spelling arm mode: {mode!r}")


def _race_class_difference(a: pd.Series, b: pd.Series) -> pd.Series:
    left = a.astype(object).astype("string")
    right = b.astype(object).astype("string")
    equal = left.eq(right).fillna(False) | (left.isna() & right.isna())
    return ~equal.astype(bool)


def _projection_hash(frame: pd.DataFrame) -> str:
    rows = frame.loc[:, _HASH_COLUMNS].itertuples(index=False, name=None)
    return frame_projection_hash(rows, _HASH_COLUMNS)


def assert_arm_identity(
    a: TrainingMatrix,
    b: TrainingMatrix,
    *,
    allowed_rows_mask: pd.Series,
) -> dict:
    """Enforce INV-A1/A2/A4/A5 and return the spelling-difference audit."""
    if a.frame is b.frame:
        raise AssertionError("INV-A5: arm frames must be different objects")
    if a.feature_cols != b.feature_cols:
        raise AssertionError("INV-A5: feature_cols differ between arms")
    if a.categorical_cols != b.categorical_cols:
        raise AssertionError("INV-A5: categorical_cols differ between arms")

    missing = [
        col for col in _HASH_COLUMNS if col not in a.frame.columns or col not in b.frame.columns
    ]
    if missing:
        raise AssertionError(f"arm frame is missing required columns: {missing}")
    try:
        pd.testing.assert_frame_equal(
            a.frame.drop(columns=[_RACE_CLASS]),
            b.frame.drop(columns=[_RACE_CLASS]),
            check_exact=True,
            check_dtype=True,
        )
    except AssertionError as exc:
        raise AssertionError(
            "INV-A1: columns other than race_class differ between arms"
        ) from exc

    if not isinstance(allowed_rows_mask, pd.Series):
        raise AssertionError("INV-A2: allowed_rows_mask must be a pandas Series")
    if not allowed_rows_mask.index.equals(a.frame.index):
        raise AssertionError("INV-A2: allowed_rows_mask index must match the arm frame")
    if not pd.api.types.is_bool_dtype(allowed_rows_mask.dtype):
        raise AssertionError("INV-A2: allowed_rows_mask must have boolean dtype")
    if allowed_rows_mask.isna().any():
        raise AssertionError("INV-A2: allowed_rows_mask must not contain nulls")

    differing = _race_class_difference(a.frame[_RACE_CLASS], b.frame[_RACE_CLASS])
    n_rows_differing = int(differing.sum())
    if n_rows_differing == 0:
        raise AssertionError("INV-A2: no differing race_class rows between arms")
    outside_allowed = differing & ~allowed_rows_mask.astype(bool)
    if outside_allowed.any():
        rows = a.frame.index[outside_allowed].tolist()
        raise AssertionError(
            f"INV-A2: race_class differs outside allowed_rows_mask at rows {rows}"
        )

    return {
        "race_class_hash_A": _projection_hash(a.frame),
        "race_class_hash_B": _projection_hash(b.frame),
        "n_rows_differing": n_rows_differing,
        "n_rows": len(a.frame),
    }
