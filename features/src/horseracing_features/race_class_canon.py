"""Feature 098: canonical representation of ``race_class`` (pure functions, no I/O).

JRA-VAN (2007..2025-10-05) spelled the win-class labels ``1勝/2勝/3勝``; netkeiba (2025-10-11..)
spells them ``１勝/２勝/３勝``. ``race_class`` is fed to the model as a raw categorical string, so
the two spellings became two categories. This module maps the netkeiba spelling onto the JRA-VAN
one — and ONLY that: ``オープン`` is a mixture of JRA-VAN ``ｵｰﾌﾟﾝ`` and ``OP(L)`` (listed) so it is
not a spelling alias, and ``重賞`` is a coarse legacy value. Anything outside the table is left
untouched and reported in the audit (constitution II: reads nothing but the column itself).

``pseudo_split`` is the inverse, used ONLY by the adoption-gate driver to impose an artificial
spelling split on historical rows (specs/098 contracts/adoption-gate.md). It is not reachable from
any production path.
"""

from __future__ import annotations

import datetime

import pandas as pd

#: netkeiba spelling -> JRA-VAN spelling. Frozen (specs/098 FR-002). Do not extend without a new
#: pre-registration: every extra entry changes what the simulation measures.
CANONICAL_TABLE: dict[str, str] = {"１勝": "1勝", "２勝": "2勝", "３勝": "3勝"}

#: The representations a feature build / artifact can declare (contracts/representation.md).
REPRESENTATIONS: tuple[str, ...] = ("raw", "canonical-v1")

#: The netkeiba-era tokens a canonical-v1 vocabulary must NOT contain (INV-R3 self-check).
SPLIT_TOKENS: frozenset[str] = frozenset(CANONICAL_TABLE)

_INVERSE: dict[str, str] = {v: k for k, v in CANONICAL_TABLE.items()}


def canonicalise(series: pd.Series) -> tuple[pd.Series, dict]:
    """Map ``１勝/２勝/３勝`` -> ``1勝/2勝/3勝``; everything else (incl. NULL) is unchanged.

    Returns ``(mapped_series, audit)`` where ``audit = {"mapped": {input: n}, "out_of_table":
    {value: n}}``. Idempotent, order-preserving, dtype-preserving (object stays object).
    """
    s = series.copy()
    hits = s.isin(CANONICAL_TABLE)
    mapped = {str(k): int(v) for k, v in s[hits].value_counts().items()}
    s[hits] = s[hits].map(CANONICAL_TABLE)
    non_null = s[~hits & s.notna()]
    out_of_table = {str(k): int(v) for k, v in non_null.value_counts().items()}
    return s, {"mapped": mapped, "out_of_table": out_of_table}


def pseudo_split(
    series: pd.Series, race_dates: pd.Series, cutoff: datetime.date
) -> pd.Series:
    """SIMULATION ONLY: re-spell ``1勝/2勝/3勝`` as ``１勝/２勝/３勝`` on rows with
    ``race_dates >= cutoff``. Rows before the cutoff, out-of-table values and NULLs are untouched.
    ``canonicalise(pseudo_split(x, d, c))`` round-trips to ``canonicalise(x)``."""
    if len(series) != len(race_dates):
        raise ValueError("series and race_dates must align")
    s = series.copy()
    dates = pd.to_datetime(pd.Series(race_dates.to_numpy(), index=s.index))
    rows = (dates >= pd.Timestamp(cutoff)) & s.isin(_INVERSE)
    s[rows] = s[rows].map(_INVERSE)
    return s
