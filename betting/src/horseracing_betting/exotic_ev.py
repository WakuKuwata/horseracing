"""Canonical field + exotic EV bet selection (research.md R1/R5, contracts/exotic_recommend.md).

CRITICAL invariant: P_model (009 on model prob p) and O_est (010 on market odds q) are computed on
ONE canonical population — horses with BOTH a valid p AND valid odds — each engine's input
renormalized over that shared set. Otherwise EV multiplies probabilities and odds from mismatched
populations. p and q are kept strictly separate (p≠q): the join happens only at EV = p_model·o_est.

Selection never reads race results (leak boundary). Per (race, bet_type): EV≥threshold, ordered by
(−EV, selection_key), truncated to top-K. P_market→0 caps O_est to None (those candidates drop).
"""

from __future__ import annotations

from collections.abc import Iterable

from horseracing_db.enums import BetType
from horseracing_probability.engine import joint_probabilities
from horseracing_probability.market_odds import estimate_market_odds

from .exotic_selection import selection_key, to_selection
from .exotic_types import ALL_EXOTIC, CanonicalField, ExcludedHorse, ExoticBet

_EPS = 1e-9


def canonical_field(
    race_id: str,
    predictions: dict[int, float | None],
    odds: dict[int, float | None],
    *,
    scratched: dict[int, str] | None = None,
    number_to_id: dict[int, str] | None = None,
) -> CanonicalField:
    """Population = horse_numbers with win_prob>0 AND odds>0 AND not scratched.

    ``scratched`` maps horse_number -> reason (cancelled/excluded). p_norm is renormalized to Σ=1
    over the population; odds_norm is the population's odds. A population of <2 yields empty dicts
    (no exotic possible) without normalizing (avoids 0-division), keeping field_size for audit.
    """
    scratched = scratched or {}
    number_to_id = number_to_id or {}
    candidates = sorted(set(predictions) | set(odds) | set(scratched))

    population: list[int] = []
    excluded: list[ExcludedHorse] = []
    for n in candidates:
        hid = number_to_id.get(n, str(n))
        if n in scratched:
            excluded.append(ExcludedHorse(n, hid, scratched[n]))
            continue
        p = predictions.get(n)
        if p is None or float(p) <= 0.0:
            excluded.append(ExcludedHorse(n, hid, "no_prob"))
            continue
        o = odds.get(n)
        if o is None or float(o) <= 0.0:
            excluded.append(ExcludedHorse(n, hid, "no_odds"))
            continue
        population.append(n)

    field_size = len(population)
    if field_size < 2:  # no exotic bet possible — do not normalize (avoid 0-division)
        return CanonicalField(race_id, population, {}, {}, field_size, excluded, number_to_id)

    total = sum(float(predictions[n]) for n in population)
    p_norm = {n: float(predictions[n]) / total for n in population}
    odds_norm = {n: float(odds[n]) for n in population}
    return CanonicalField(
        race_id, population, p_norm, odds_norm, field_size, excluded, number_to_id
    )


def _k_for(top_k: int | dict[str, int], bet_type: str) -> int:
    if isinstance(top_k, dict):
        return int(top_k.get(bet_type, 0))
    return int(top_k)


def candidate_bets(
    field: CanonicalField,
    *,
    bet_types: Iterable[str] = ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
) -> dict[str, list[ExoticBet]]:
    """All scoreable candidates per bet type (no threshold/top-K) on the SHARED canonical field.

    P_model from 009(p), O_est from 010(q), keyed identically (same int horse_numbers). Used by both
    the EV strategy and the ROI baselines so they compare on one population/selection/odds path.
    """
    if not field.p_norm:
        return {}

    joint = joint_probabilities(field.p_norm, field_size=field.field_size)
    est = estimate_market_odds(
        field.odds_norm, field_size=field.field_size, payout_rates=payout_rates, odds_cap=odds_cap
    )
    pmaps = {
        BetType.PLACE: joint.place, BetType.QUINELLA: joint.quinella, BetType.EXACTA: joint.exacta,
        BetType.WIDE: joint.wide, BetType.TRIO: joint.trio, BetType.TRIFECTA: joint.trifecta,
    }
    omaps = {
        BetType.PLACE: est.place, BetType.QUINELLA: est.quinella, BetType.EXACTA: est.exacta,
        BetType.WIDE: est.wide, BetType.TRIO: est.trio, BetType.TRIFECTA: est.trifecta,
    }

    out: dict[str, list[ExoticBet]] = {}
    for bt in bet_types:
        p_map = pmaps.get(bt)
        o_map = omaps.get(bt)
        if not p_map or not o_map:  # None (field rule / N<3) or empty
            continue
        cands: list[ExoticBet] = []
        for key, p_model in p_map.items():
            if p_model <= 0.0:
                continue
            o_est = o_map.get(key)
            if o_est is None:  # P_market→0 capped to None
                continue
            sel = to_selection(bt, key)
            cands.append(ExoticBet(bt, sel, float(p_model), float(o_est), float(p_model * o_est)))
        if cands:
            out[bt] = cands
    return out


def exotic_ev_bets(
    field: CanonicalField,
    *,
    threshold: float = 1.0,
    top_k: int | dict[str, int] = 5,
    bet_types: Iterable[str] = ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
) -> list[ExoticBet]:
    """EV = P_model(009 on p) × O_est(010 on q) on the canonical field; EV≥threshold, top-K."""
    cands = candidate_bets(
        field, bet_types=bet_types, payout_rates=payout_rates, odds_cap=odds_cap
    )
    out: list[ExoticBet] = []
    for bt, bets in cands.items():
        k = _k_for(top_k, bt)
        if k <= 0:
            continue
        kept = [b for b in bets if b.ev >= threshold - _EPS]
        kept.sort(key=lambda b: (-b.ev, selection_key(b.bet_type, b.selection)))
        out.extend(kept[:k])
    return out
