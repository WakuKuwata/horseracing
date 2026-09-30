"""Read-only assembly of the Feature 137 market-aware expected return (期待回収率).

The values come from a SEPARATE market-aware model (the current win odds are among its inputs) and
are stored per started horse in ``market_ev_predictions`` by the training job. This module only
shapes stored rows into the response; it never recomputes a probability and imports no ML package.
It is independent of the win-probability model selection (the endpoint takes no model_version).

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
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from .schemas import HorseMarketEv, MarketEvAvailable, MarketEvUnavailable

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
    expected_return: Decimal | float
    odds_used: Decimal | float
    odds_observed_at: datetime.datetime
    result_pending_at_compute: bool
    logic_version: str
    computed_at: datetime.datetime


def exceeds_threshold(expected_return: float) -> bool:
    """True only when the expected return is STRICTLY above the threshold (1.2 itself is not)."""
    return expected_return > MARKET_EV_THRESHOLD


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
) -> MarketEvAvailable | MarketEvUnavailable:
    """Shape the stored rows of ONE market-aware model_version for ``race_id``.

    ``current_started_odds`` maps every CURRENTLY started horse_id of the race to its current win
    odds (None when missing). Rows of more than one model_version are a caller error.
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

    horses: list[HorseMarketEv] = []
    for row in sorted(rows, key=_order_key):
        expected_return = float(row.expected_return)
        horses.append(
            HorseMarketEv(
                horse_id=row.horse_id,
                horse_number=(int(row.horse_number) if row.horse_number is not None else None),
                expected_return=expected_return,
                odds_used=float(row.odds_used),
                exceeds_threshold=exceeds_threshold(expected_return),
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
        horses=horses,
    )
