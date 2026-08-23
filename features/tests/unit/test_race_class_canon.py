"""Feature 098: the race_class canonical spelling table is narrow and reversible."""

from __future__ import annotations

import datetime

import pandas as pd

from horseracing_features.race_class_canon import canonicalise, pseudo_split


def test_canonicalise_maps_only_the_three_declared_spellings_with_audit():
    values = pd.Series(
        ["１勝", "２勝", "３勝", "１勝", "オープン", "重賞", "新馬", "Ｇ１", None, "1勝"],
        index=[19, 3, 41, 7, 11, 5, 29, 2, 23, 13],
        dtype=object,
    )
    expected = pd.Series(
        ["1勝", "2勝", "3勝", "1勝", "オープン", "重賞", "新馬", "Ｇ１", None, "1勝"],
        index=values.index,
        dtype=object,
    )

    actual, audit = canonicalise(values)

    pd.testing.assert_series_equal(actual, expected)
    assert actual.dtype == object
    assert actual.index.tolist() == values.index.tolist()
    assert audit == {
        "mapped": {"１勝": 2, "２勝": 1, "３勝": 1},
        "out_of_table": {"オープン": 1, "重賞": 1, "新馬": 1, "Ｇ１": 1, "1勝": 1},
    }
    assert sum(audit["mapped"].values()) == 4
    assert sum(audit["out_of_table"].values()) == 5


def test_canonicalise_is_idempotent():
    values = pd.Series(["１勝", "２勝", "３勝", "オープン", None, "1勝"], dtype=object)

    once, _ = canonicalise(values)
    twice, _ = canonicalise(once)

    pd.testing.assert_series_equal(twice, once)


def test_pseudo_split_changes_only_declared_tokens_at_or_after_cutoff():
    values = pd.Series(
        ["1勝", "2勝", "3勝", "1勝", "2勝", "3勝", "オープン", None, "１勝"],
        index=[10, 4, 18, 1, 30, 9, 14, 6, 22],
        dtype=object,
    )
    dates = pd.Series(
        [
            datetime.date(2022, 12, 31),
            datetime.date(2022, 12, 31),
            datetime.date(2022, 12, 31),
            datetime.date(2023, 1, 1),
            datetime.date(2023, 6, 1),
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 1),
        ],
        index=values.index,
    )
    expected = pd.Series(
        ["1勝", "2勝", "3勝", "１勝", "２勝", "３勝", "オープン", None, "１勝"],
        index=values.index,
        dtype=object,
    )

    actual = pseudo_split(values, dates, datetime.date(2023, 1, 1))

    pd.testing.assert_series_equal(actual, expected)
    assert actual.dtype == object
    assert actual.index.tolist() == values.index.tolist()

    round_tripped, _ = canonicalise(actual)
    canonical_original, _ = canonicalise(values)
    pd.testing.assert_series_equal(round_tripped, canonical_original)
