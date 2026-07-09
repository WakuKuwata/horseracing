"""Class-rank normalization for class_transition (Feature 020 bugfix).

Regression guard for the silent data bug: the raw DB race_class carries full/half-width variants
(Ｇ３, ｵｰﾌﾟﾝ, １勝) and the pre-2019 money-class naming (500万/1000万/1600万) that coexists with the
post-2019 win-class naming (1勝/2勝/3勝). The old _CLASS_RANK matched only the suffixed canonical
strings, leaving 55.7% of races NaN → class_transition was effectively dead. Synthetic feature tests
never caught it because they only ever used race_class="未勝利" (which happened to match).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from horseracing_features.extra_features import _CLASS_RANK, _normalize_class


def _rank(raw: str) -> float:
    return _normalize_class(pd.Series([raw])).map(_CLASS_RANK).iloc[0]


@pytest.mark.parametrize(
    "raw, expected",
    [
        # low classes (already worked before the fix)
        ("未勝利", 0), ("新馬", 0),
        # pre-2019 money naming == post-2019 win naming (the bulk of the 55.7% that was NaN)
        ("500万", 1), ("1勝", 1), ("1勝クラス", 1),
        ("1000万", 2), ("2勝", 2),
        ("1600万", 3), ("3勝", 3),
        # full-width digit variants (NFKC folds １→1, etc.)
        ("１勝", 1), ("２勝", 2), ("３勝", 3),
        # open / listed: half-width kana ｵｰﾌﾟﾝ and OP(L) both resolve
        ("ｵｰﾌﾟﾝ", 4), ("オープン", 4), ("OP(L)", 4),
        # graded: full-width Ｇ３/Ｇ２/Ｇ１ fold to G3/G2/G1
        ("Ｇ３", 5), ("Ｇ２", 6), ("Ｇ１", 7),
    ],
)
def test_db_race_class_variants_resolve_to_rank(raw: str, expected: int) -> None:
    assert _rank(raw) == expected


@pytest.mark.parametrize("raw", ["ＪＧ３", "ＪＧ２", "ＪＧ１", "重賞", "なんとか", ""])
def test_ambiguous_or_jump_grades_stay_nan(raw: str) -> None:
    # jump grades (障害重賞) and the ambiguous 重賞 are intentionally unmapped: jumps are a separate
    # ladder (excluded from the flat model) so a jump prior yields no comparable class transition.
    assert math.isnan(_rank(raw))


def test_missing_class_stays_nan_rank() -> None:
    # NULL race_class (None) and defensive non-strings (nan) must resolve to a NaN rank, never a
    # fabricated class, and must not crash the normalize→map pipeline.
    out = _normalize_class(pd.Series([None, float("nan")], dtype=object)).map(_CLASS_RANK)
    assert math.isnan(out.iloc[0])
    assert math.isnan(out.iloc[1])
