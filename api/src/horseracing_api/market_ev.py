"""Read-only assembly of the Feature 137 market-aware expected return (期待回収率).

The values come from a SEPARATE market-aware model (the current win odds are among its inputs) and
are stored per started horse in ``market_ev_predictions`` by the training job. This module only
shapes stored rows into the response; it never recomputes a probability and imports no ML package.
It is independent of the win-probability model selection (the endpoint takes no model_version).

Feature 138: the endpoint shows ONE fixed model version, ``DISPLAYED_MARKET_EV_MODEL_VERSION``
(the 15-seed average), imported from the eval registry and never re-declared here. The former
"most recently computed version" selection is gone: with two versions stored per race, a manual
single-seed recompute must not silently swap the column back to the single-seed series.

``MARKET_EV_THRESHOLD`` is the single source of truth for the highlight threshold. The unit is a
ratio (1.2 = 120%; percent formatting belongs to the front) and the comparison is STRICT:
``expected_return > MARKET_EV_THRESHOLD``, so exactly 1.2 is not flagged.

States (all 200):
- no stored rows for the race -> ``unavailable(not_computed)``
- stored horse_id set != current started horse_id set -> ``unavailable(field_changed)``
- a currently started horse has no win odds, or odds below 1.0 -> ``unavailable(odds_unavailable)``
  (the training job never computes such a race, so any stored rows are from an earlier price)
- otherwise ``available``. ``odds_changed_after_compute`` compares each stored horse's CURRENT win
  odds with its stored ``odds_used`` by value (never by timestamp).
  ``result_pending_at_compute`` is true only when every stored row says so.

``win_prob`` is deliberately not part of the response (constitution IV: the raw binary output is
not normalized within the race, so it must never be shown as a 1着率).

Feature 141: an ``available`` response also carries the MARKET-MIXED expected return per horse
(``expected_return_mix``), computed here at read time from the stored ``win_prob`` / ``odds_used``
of the whole started field with the shared definition ``horseracing_eval.market_mix`` (nothing is
stored). It is a parallel value: ``expected_return``, the 1.2 highlight and S1–S5 are unchanged.
``mix`` says how it was computed, or why it is absent (``no_coefficients`` for a year without
frozen coefficients, ``invalid_inputs``, ``model_mismatch`` when the rows are not the model the
coefficients were fitted on, ``disabled``) — the mixed column is then null, never back-filled with
the unmixed value. The mixed probability itself is not returned either.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from horseracing_eval import market_mix
from horseracing_eval.attention_rules import DISPLAYED_MARKET_EV_MODEL_VERSION

from .schemas import (
    HorseMarketEv,
    MarketEvAvailable,
    MarketEvUnavailable,
    MarketMixAvailable,
    MarketMixUnavailable,
)

__all__ = [
    "DISPLAYED_MARKET_EV_MODEL_VERSION",
    "MARKET_EV_THRESHOLD",
    "StoredMarketEv",
    "build_market_ev",
    "exceeds_threshold",
]

#: The ONLY place the highlight threshold is defined (ratio, strict ``>``).
MARKET_EV_THRESHOLD = 1.2

#: Odds are quoted to 0.1; anything above this is a real difference, not float noise.
_ODDS_TOLERANCE = 1e-9


class StoredMarketEv(Protocol):
    """The attributes read from one stored ``market_ev_predictions`` row."""

    race_id: str
    horse_id: str
    model_version: str
    horse_number: int | None
    win_prob: Decimal | float
    expected_return: Decimal | float
    odds_used: Decimal | float
    booster: str
    odds_observed_at: datetime.datetime
    result_pending_at_compute: bool
    logic_version: str
    computed_at: datetime.datetime


def exceeds_threshold(expected_return: float) -> bool:
    """True only when the expected return is STRICTLY above the threshold (1.2 itself is not)."""
    return expected_return > MARKET_EV_THRESHOLD


def _mix(
    rows: Sequence[StoredMarketEv], race_date: datetime.date | None
) -> tuple[MarketMixAvailable | MarketMixUnavailable, list[float] | None]:
    """The mixed expected returns of ``rows`` (the whole started field, in ``rows`` order)."""
    threshold = market_mix.MIX_EV_THRESHOLD

    def unavailable(reason: str):
        return MarketMixUnavailable(status="unavailable", reason=reason, threshold=threshold), None

    if not market_mix.MIX_DISPLAY_ENABLED:
        return unavailable("disabled")
    if {row.model_version for row in rows} != {market_mix.MIX_SOURCE_MODEL_VERSION}:
        return unavailable("model_mismatch")
    coef = market_mix.coefficients_for(race_date.year) if race_date is not None else None
    if coef is None:
        return unavailable("no_coefficients")
    # the coefficients of year Y were fitted for the p̄ of the year-Y booster manifest; a row
    # computed with another year's manifest (the loader falls back to an earlier year when the
    # race year has none) is a different p̄ (review L2)
    if {row.booster for row in rows} != {f"ensemble_{coef.year}.json"}:
        return unavailable("model_mismatch")
    odds = [float(row.odds_used) for row in rows]
    try:
        p_mix = market_mix.mix_probabilities([float(row.win_prob) for row in rows], odds, coef)
    except ValueError:
        return unavailable("invalid_inputs")
    info = MarketMixAvailable(
        status="available",
        version=market_mix.MIX_VERSION,
        source_model_version=market_mix.MIX_SOURCE_MODEL_VERSION,
        coef_year=coef.year,
        fit_through_year=coef.fit_through_year,
        a=coef.a,
        b=coef.b,
        threshold=threshold,
    )
    return info, [float(p) * o for p, o in zip(p_mix, odds, strict=True)]


def _odds_differ(current: Decimal | float | None, used: Decimal | float) -> bool:
    if current is None:
        return True
    return abs(float(current) - float(used)) > _ODDS_TOLERANCE


def _order_key(row: StoredMarketEv) -> tuple[bool, int, str]:
    number = row.horse_number
    return (number is None, int(number) if number is not None else 0, row.horse_id)


def build_market_ev(
    race_id: str,
    rows: Sequence[StoredMarketEv],
    current_started_odds: Mapping[str, Decimal | float | None],
    *,
    race_date: datetime.date | None,
) -> MarketEvAvailable | MarketEvUnavailable:
    """Shape the stored rows of ONE market-aware model_version for ``race_id``.

    ``current_started_odds`` maps every CURRENTLY started horse_id of the race to its current win
    odds (None when missing). Rows of more than one model_version are a caller error.
    ``race_date`` picks the mixture coefficients (Feature 141; required — no silent default; a
    race without a date gets ``mix: unavailable(no_coefficients)``).
    """
    if not rows:
        return MarketEvUnavailable(
            status="unavailable",
            race_id=race_id,
            reason="not_computed",
            threshold=MARKET_EV_THRESHOLD,
        )
    versions = {row.model_version for row in rows}
    if len(versions) != 1:
        raise ValueError(f"market-ev rows mix model versions: {sorted(versions)}")
    if {row.horse_id for row in rows} != set(current_started_odds):
        return MarketEvUnavailable(
            status="unavailable",
            race_id=race_id,
            reason="field_changed",
            threshold=MARKET_EV_THRESHOLD,
        )

    if any(odds is None or float(odds) < 1.0 for odds in current_started_odds.values()):
        return MarketEvUnavailable(
            status="unavailable",
            race_id=race_id,
            reason="odds_unavailable",
            threshold=MARKET_EV_THRESHOLD,
        )

    ordered = sorted(rows, key=_order_key)
    mix_info, mixed = _mix(ordered, race_date)
    horses: list[HorseMarketEv] = []
    for i, row in enumerate(ordered):
        expected_return = float(row.expected_return)
        ev_mix = mixed[i] if mixed is not None else None
        horses.append(
            HorseMarketEv(
                horse_id=row.horse_id,
                horse_number=(int(row.horse_number) if row.horse_number is not None else None),
                expected_return=expected_return,
                odds_used=float(row.odds_used),
                exceeds_threshold=exceeds_threshold(expected_return),
                expected_return_mix=ev_mix,
                exceeds_mix_threshold=(
                    market_mix.exceeds_mix_threshold(ev_mix) if ev_mix is not None else None
                ),
            )
        )
    latest = max(rows, key=lambda row: row.computed_at)
    return MarketEvAvailable(
        status="available",
        race_id=race_id,
        model_version=latest.model_version,
        logic_version=latest.logic_version,
        computed_at=latest.computed_at,
        odds_observed_at=max(row.odds_observed_at for row in rows),
        odds_changed_after_compute=any(
            _odds_differ(current_started_odds[row.horse_id], row.odds_used) for row in rows
        ),
        result_pending_at_compute=all(row.result_pending_at_compute for row in rows),
        threshold=MARKET_EV_THRESHOLD,
        is_pseudo=True,
        mix=mix_info,
        horses=horses,
    )
