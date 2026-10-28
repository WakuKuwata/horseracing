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

B7 (2026-10-28): ``build_market_ev_day`` folds the SAME per-race shaping over one race day for the
date listing (``/market-ev/day``): per race its state, provenance and the counts of horses above
the two thresholds — never a per-horse value, never ordered by a value (board order only).

B11 (2026-10-28): ``market_ev_coverage_by_date`` counts, per race day, the races whose STORED rows
of the displayed version exist / carry 140's genuine-quote token on every row / let the 141
mixture be computed (the same ``_mix`` checks the race page runs, on the stored rows alone — the
current-odds conditions ``field_changed``/``odds_unavailable`` are the day listing's business, not
the coverage page's). Feeds the admin coverage page (052) so its holes are ROI holes.
"""

from __future__ import annotations

import datetime
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from horseracing_eval import market_mix
from horseracing_eval.attention_rules import DISPLAYED_MARKET_EV_MODEL_VERSION

from .schemas import (
    HorseMarketEv,
    MarketEvAvailable,
    MarketEvDayItem,
    MarketEvDayResponse,
    MarketEvUnavailable,
    MarketMixAvailable,
    MarketMixUnavailable,
)

__all__ = [
    "DISPLAYED_MARKET_EV_MODEL_VERSION",
    "MARKET_EV_THRESHOLD",
    "QUOTE_TOKEN",
    "DayRace",
    "MarketEvCoverage",
    "StoredMarketEv",
    "build_market_ev",
    "build_market_ev_day",
    "exceeds_threshold",
    "has_quote_token",
    "market_ev_coverage_by_date",
    "stored_mix_available",
]

#: The ONLY place the highlight threshold is defined (ratio, strict ``>``).
MARKET_EV_THRESHOLD = 1.2

#: Odds are quoted to 0.1; anything above this is a real difference, not float noise.
_ODDS_TOLERANCE = 1e-9

#: Feature 140's per-row token for a genuine win-quote observation (training's constant; a row
#: whose observation time is the ``updated_at`` proxy carries ``oddsobs=proxy`` instead).
QUOTE_TOKEN = "oddsobs=quote"


def has_quote_token(logic_version: str) -> bool:
    """True when ``logic_version`` carries ``oddsobs=quote`` as a whole ``;``-separated token
    (never a substring match: ``oddsobs=quoteX`` would not count)."""
    return QUOTE_TOKEN in [seg.strip() for seg in str(logic_version).split(";")]


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


def stored_mix_available(rows: Sequence[StoredMarketEv], race_date: datetime.date | None) -> bool:
    """Whether the 141 mixture can be computed from the stored rows of ONE race (the same checks
    ``build_market_ev`` runs: display flag, source version, frozen coefficients for the race year,
    the matching booster, valid inputs). The race page can still be ``unavailable`` for reasons
    that need the current odds; those are not judged here."""
    return _mix(rows, race_date)[0].status == "available"


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


class DayRace(Protocol):
    """The race columns the day listing reads (a ``races`` row)."""

    race_id: str
    race_date: datetime.date | None
    venue_code: str | None
    race_number: int | None
    post_time: datetime.datetime | None


def build_market_ev_day(
    *,
    date: datetime.date,
    races: Sequence[DayRace],
    rows_by_race: Mapping[str, Sequence[StoredMarketEv]],
    started_odds_by_race: Mapping[str, Mapping[str, Decimal | float | None]],
    race_ids_with_results: Collection[str],
) -> MarketEvDayResponse:
    """The day listing: one item per race of ``races`` (already in board order) that has stored
    rows of the displayed version, each folded from ``build_market_ev`` so that the listing can
    never disagree with the race page (an ``unavailable`` race page is an ``unavailable`` item
    with null counts). Races without rows are left out and only counted in ``n_races``.
    """
    items: list[MarketEvDayItem] = []
    for race in races:
        rows = rows_by_race.get(race.race_id) or ()
        if not rows:
            continue
        resp = build_market_ev(
            race.race_id,
            rows,
            started_odds_by_race.get(race.race_id, {}),
            race_date=race.race_date,
        )
        common = dict(
            race_id=race.race_id,
            venue_code=race.venue_code,
            race_number=race.race_number,
            post_time=race.post_time,
            has_results=race.race_id in race_ids_with_results,
        )
        if isinstance(resp, MarketEvUnavailable):
            if resp.reason == "not_computed":  # impossible with rows; keep the fold total
                continue
            items.append(MarketEvDayItem(
                **common, status="unavailable", reason=resp.reason,
                computed_at=None, odds_observed_at=None, odds_changed_after_compute=None,
                result_pending_at_compute=None, n_horses=None, n_exceeds_threshold=None,
                mix_status=None, n_exceeds_mix_threshold=None,
            ))
            continue
        mix_available = resp.mix.status == "available"
        items.append(MarketEvDayItem(
            **common,
            status="available",
            reason=None,
            computed_at=resp.computed_at,
            odds_observed_at=resp.odds_observed_at,
            odds_changed_after_compute=resp.odds_changed_after_compute,
            result_pending_at_compute=resp.result_pending_at_compute,
            n_horses=len(resp.horses),
            n_exceeds_threshold=sum(1 for h in resp.horses if h.exceeds_threshold),
            mix_status=resp.mix.status,
            n_exceeds_mix_threshold=(
                sum(1 for h in resp.horses if h.exceeds_mix_threshold) if mix_available else None
            ),
        ))
    return MarketEvDayResponse(
        date=date,
        model_version=DISPLAYED_MARKET_EV_MODEL_VERSION,
        threshold=MARKET_EV_THRESHOLD,
        mix_threshold=market_mix.MIX_EV_THRESHOLD,
        n_races=len(races),
        n_computed=len(items),
        n_available=sum(1 for i in items if i.status == "available"),
        items=items,
    )


class MarketEvCoverage(Protocol):
    """The stored-row columns the coverage fold reads (a ``market_ev_coverage_rows`` row)."""

    race_id: str
    race_date: datetime.date | None
    model_version: str
    logic_version: str
    booster: str
    win_prob: Decimal | float
    odds_used: Decimal | float


def market_ev_coverage_by_date(
    rows: Sequence[MarketEvCoverage],
) -> dict[datetime.date, dict[str, int]]:
    """B11: per race day, how many races have stored rows of the model version the rows belong
    to (``computed``), how many of those carry the genuine-quote token on EVERY row (``quote``),
    and how many of those can have the 141 mixture computed from the stored rows
    (``mix_available``). Rows are grouped by race; a race whose rows span two dates is a data
    error and raises. Days without rows are simply absent (the caller fills 0)."""
    by_race: dict[str, list[MarketEvCoverage]] = {}
    for row in rows:
        by_race.setdefault(row.race_id, []).append(row)
    out: dict[datetime.date, dict[str, int]] = {}
    for race_id, race_rows in by_race.items():
        dates = {r.race_date for r in race_rows}
        if len(dates) != 1:
            raise ValueError(f"market-ev rows of race {race_id} span several dates: {dates}")
        (race_date,) = dates
        if race_date is None:
            continue
        day = out.setdefault(race_date, {"computed": 0, "quote": 0, "mix_available": 0})
        day["computed"] += 1
        if all(has_quote_token(r.logic_version) for r in race_rows):
            day["quote"] += 1
        if stored_mix_available(race_rows, race_date):
            day["mix_available"] += 1
    return out
