"""Feature 098: training-matrix race_class representation is explicit and fail-closed."""

from __future__ import annotations

import pandas as pd
import pytest

from horseracing_training import dataset
from horseracing_training.dataset import apply_race_class_representation


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "race_class": pd.Series(
                ["１勝", "２勝", "３勝", "１勝", "オープン", None, "1勝"], dtype=object
            ),
            "distance": [1200, 1400, 1600, 1800, 2000, 2200, 2400],
        }
    )


def _object_values(series: pd.Series) -> list:
    values = series.astype(object)
    return values.where(values.notna(), None).tolist()


def test_canonical_representation_maps_three_tokens_and_keeps_audit():
    original = _frame()

    actual, audit = apply_race_class_representation(original, "canonical-v1")

    assert _object_values(actual["race_class"]) == [
        "1勝",
        "2勝",
        "3勝",
        "1勝",
        "オープン",
        None,
        "1勝",
    ]
    assert actual["race_class"].dtype.name == "category"
    assert audit == {
        "mapped": {"１勝": 2, "２勝": 1, "３勝": 1},
        "out_of_table": {"オープン": 1, "1勝": 1},
    }
    # Pure helper: callers may retain the raw frame for a paired comparison.
    assert original["race_class"].tolist() == [
        "１勝",
        "２勝",
        "３勝",
        "１勝",
        "オープン",
        None,
        "1勝",
    ]
    assert original["race_class"].dtype == object


def test_raw_representation_is_value_noop_with_empty_audit():
    original = _frame()

    actual, audit = apply_race_class_representation(original, "raw")

    assert _object_values(actual["race_class"]) == original["race_class"].tolist()
    assert actual["race_class"].dtype.name == "category"
    assert audit == {}
    assert original["race_class"].dtype == object


def test_unknown_representation_is_rejected():
    with pytest.raises(ValueError, match="unknown race_class representation"):
        apply_race_class_representation(_frame(), "canonical-v2")


def test_category_coercion_must_not_introduce_nan(monkeypatch):
    def _inject_nan(series: pd.Series) -> pd.Series:
        coerced = series.astype("category")
        coerced.iloc[0] = None
        return coerced

    monkeypatch.setattr(dataset, "_astype_category", _inject_nan)

    with pytest.raises(RuntimeError, match="introduced NaN"):
        apply_race_class_representation(_frame(), "canonical-v1")
