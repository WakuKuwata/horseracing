"""Contract tests for deterministic race-level weight masking."""

from __future__ import annotations

import pandas as pd
import pytest

from horseracing_features.weight_mask import (
    WEIGHT_MASK_COLUMNS,
    MaskSpec,
    apply_weight_mask,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "race_id": ["race-a", "race-a", "race-b", "race-b", "race-c", "race-c"],
            "horse_id": ["a1", "a2", "b1", "b2", "c1", "c2"],
            "weight": [470.0, 482.0, 455.0, 501.0, 468.0, 490.0],
            "weight_diff": [-2.0, 4.0, 0.0, 8.0, -6.0, 2.0],
            "carried_weight_ratio": [0.117, 0.116, 0.121, 0.109, 0.120, 0.113],
            "prev_weight": [472.0, 478.0, 455.0, 493.0, 474.0, 488.0],
            "finish_order": [1, 2, 4, 3, 6, 5],
            "odds": [2.1, 3.8, 12.4, 7.2, 18.0, 9.5],
        },
        index=pd.Index([101, 105, 203, 207, 309, 311], name="entry_index"),
    )


def _masked_race_ids(frame: pd.DataFrame) -> set[str]:
    masked_rows = frame.loc[:, list(WEIGHT_MASK_COLUMNS)].isna().all(axis=1)
    return set(frame.loc[masked_rows, "race_id"])


def test_weight_mask_columns_are_frozen() -> None:
    assert WEIGHT_MASK_COLUMNS == ("weight", "weight_diff", "carried_weight_ratio")


def test_none_returns_an_exact_frame_copy() -> None:
    frame = _frame()

    result = apply_weight_mask(frame, spec=None)

    assert result is not frame
    pd.testing.assert_frame_equal(result, frame, check_exact=True)
    assert result.columns.equals(frame.columns)
    assert result.index.equals(frame.index)
    assert result.dtypes.equals(frame.dtypes)


def test_none_does_not_require_race_or_mask_columns() -> None:
    frame = pd.DataFrame({"unrelated": [1, 2]}, index=pd.Index([9, 4], name="row"))

    result = apply_weight_mask(frame, spec=None)

    pd.testing.assert_frame_equal(result, frame, check_exact=True)


def test_selected_races_only_change_mask_columns() -> None:
    frame = _frame()
    original = frame.copy(deep=True)

    result = apply_weight_mask(frame, spec=MaskSpec(rate=0.5, seed=1729))
    masked_races = _masked_race_ids(result)

    pd.testing.assert_frame_equal(frame, original, check_exact=True)
    assert masked_races
    assert masked_races != set(frame["race_id"])
    masked_rows = frame["race_id"].isin(masked_races)
    assert result.loc[masked_rows, list(WEIGHT_MASK_COLUMNS)].isna().all().all()
    pd.testing.assert_frame_equal(
        result.loc[~masked_rows],
        frame.loc[~masked_rows],
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        result.drop(columns=list(WEIGHT_MASK_COLUMNS)),
        frame.drop(columns=list(WEIGHT_MASK_COLUMNS)),
        check_exact=True,
    )


@pytest.mark.parametrize("missing_column", WEIGHT_MASK_COLUMNS)
def test_missing_mask_column_fails_closed(missing_column: str) -> None:
    frame = _frame().drop(columns=missing_column)

    with pytest.raises(KeyError, match=missing_column):
        apply_weight_mask(frame, spec=MaskSpec(rate=0.0, seed=1))


def test_missing_race_id_fails_closed() -> None:
    with pytest.raises(KeyError, match="race_id"):
        apply_weight_mask(_frame().drop(columns="race_id"), spec=MaskSpec(rate=0.5, seed=1))


def test_prev_weight_is_never_masked() -> None:
    frame = _frame()

    result = apply_weight_mask(frame, spec=MaskSpec(rate=1.0, seed=99))

    pd.testing.assert_series_equal(result["prev_weight"], frame["prev_weight"], check_exact=True)
    assert result.loc[:, list(WEIGHT_MASK_COLUMNS)].isna().all().all()


def test_mask_is_deterministic_and_independent_of_row_order_and_partitions() -> None:
    frame = _frame()
    spec = MaskSpec(rate=0.5, seed=314159)

    combined = apply_weight_mask(frame, spec=spec)
    repeated = apply_weight_mask(frame, spec=spec)
    shuffled = apply_weight_mask(frame.sample(frac=1.0, random_state=7), spec=spec).sort_index()
    split = pd.concat(
        [
            apply_weight_mask(frame.iloc[::2], spec=spec),
            apply_weight_mask(frame.iloc[1::2], spec=spec),
        ]
    ).sort_index()

    pd.testing.assert_frame_equal(repeated, combined, check_exact=True)
    pd.testing.assert_frame_equal(shuffled, combined, check_exact=True)
    pd.testing.assert_frame_equal(split, combined, check_exact=True)


def test_seed_participates_in_race_selection() -> None:
    frame = _frame()

    first = apply_weight_mask(frame, spec=MaskSpec(rate=0.5, seed=1729))
    second = apply_weight_mask(frame, spec=MaskSpec(rate=0.5, seed=1730))

    assert _masked_race_ids(first) == {"race-a", "race-b"}
    assert _masked_race_ids(second) == {"race-b", "race-c"}


@pytest.mark.parametrize(
    ("rate", "expected_masked_count"),
    [(0.0, 0), (1.0, 3)],
)
def test_rate_boundaries(rate: float, expected_masked_count: int) -> None:
    frame = _frame()

    result = apply_weight_mask(frame, spec=MaskSpec(rate=rate, seed=123))

    assert len(_masked_race_ids(result)) == expected_masked_count
    if rate == 0.0:
        pd.testing.assert_frame_equal(result, frame, check_exact=True)
    else:
        assert result.loc[:, list(WEIGHT_MASK_COLUMNS)].isna().all().all()
        pd.testing.assert_frame_equal(
            result.drop(columns=list(WEIGHT_MASK_COLUMNS)),
            frame.drop(columns=list(WEIGHT_MASK_COLUMNS)),
            check_exact=True,
        )


def test_mask_is_atomic_within_each_race() -> None:
    result = apply_weight_mask(_frame(), spec=MaskSpec(rate=0.5, seed=1729))
    row_is_masked = result.loc[:, list(WEIGHT_MASK_COLUMNS)].isna().all(axis=1)

    assert row_is_masked.groupby(result["race_id"]).nunique().eq(1).all()


def test_unit_other_than_race_is_rejected() -> None:
    with pytest.raises(ValueError, match="unit"):
        MaskSpec(rate=0.5, seed=1, unit="row")


@pytest.mark.parametrize("rate", [-0.01, 1.01, float("nan")])
def test_rate_outside_closed_unit_interval_is_rejected(rate: float) -> None:
    with pytest.raises(ValueError, match="rate"):
        MaskSpec(rate=rate, seed=1)


def test_mask_selection_does_not_depend_on_results_or_odds() -> None:
    frame = _frame()
    changed_outcomes = frame.copy()
    changed_outcomes["finish_order"] = [99, 98, 97, 96, 95, 94]
    changed_outcomes["odds"] = [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    spec = MaskSpec(rate=0.5, seed=1729)

    original_result = apply_weight_mask(frame, spec=spec)
    changed_result = apply_weight_mask(changed_outcomes, spec=spec)

    assert _masked_race_ids(original_result) == _masked_race_ids(changed_result)
