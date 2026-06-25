"""Estimated market odds from WIN odds (contracts/market_odds.md, INV-M1..M8).

odds_i -> market-implied win prob q_i=(1/odds_i)/Σ(1/odds_j) (the market VOTE SHARE — NOT a true
win prob and NOT the model prob p; it carries favorite-longshot bias) -> feed q to the Feature
009 engine -> per-bet-type market-implied prob -> estimated odds = (1 - takeout_b)/P_market.
The conversion reads market odds ONLY (never model p; p and q are kept separate). A derived odds
of None/cap protects against P->0; the probability itself is never capped (consistency intact).
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import joint_probabilities

#: payout_rate R_b = 1 - takeout. JRA defaults (since 2014-06-07); configurable.
DEFAULT_PAYOUT_RATES: dict[str, float] = {
    "win": 0.80, "place": 0.80, "quinella": 0.775, "wide": 0.775,
    "exacta": 0.75, "trio": 0.75, "trifecta": 0.725,
}
DEFAULT_ODDS_CAP = 10000.0
_EPS = 1e-12


class MarketOddsError(ValueError):
    """Raised when win odds cannot yield a market-implied distribution."""


def market_implied_win_probs(win_odds: dict[str, float]) -> dict[str, float]:
    """q_i = (1/odds_i) / Σ_j(1/odds_j) over horses with valid (>0) odds. Σq=1.

    This is the market vote share — explicitly NOT a true win probability and NOT the model p.
    """
    inv = {h: 1.0 / float(o) for h, o in win_odds.items() if o is not None and float(o) > 0.0}
    if not inv:
        raise MarketOddsError("no valid (>0) win odds")
    s = sum(inv.values())
    if s <= 0.0:
        raise MarketOddsError("Σ(1/odds) <= 0")
    return {h: v / s for h, v in inv.items()}


@dataclass(frozen=True)
class EstimatedOdds:
    win: dict[str, float | None]
    place: dict[str, float | None] | None
    exacta: dict[tuple[str, str], float | None]
    quinella: dict[frozenset[str], float | None]
    wide: dict[frozenset[str], float | None] | None
    trifecta: dict[tuple[str, str, str], float | None]
    trio: dict[frozenset[str], float | None]
    payout_rates: dict[str, float]
    is_estimated: bool = True


def _odds_from_prob(p: float, payout_rate: float, cap: float) -> float | None:
    if p <= _EPS:
        return None                       # can't price a ~zero-probability combination
    return min(payout_rate / p, cap)      # cap the DERIVED odds, never the probability


def estimate_market_odds(
    win_odds: dict[str, float],
    *,
    field_size: int | None = None,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = DEFAULT_ODDS_CAP,
    calibrator=None,
) -> EstimatedOdds:
    """Estimated exotic odds from WIN odds. With ``calibrator`` (Feature 013), the market vote
    share q is FL-bias-corrected to q' before the 009 engine; without it, raw q (backward
    compatible). q'/q are market-derived, never the model p (p≠q)."""
    rates = {**DEFAULT_PAYOUT_RATES, **(payout_rates or {})}
    if calibrator is not None:
        from .fl_bias import apply_calibrator  # lazy: avoid import cycle
        cp = apply_calibrator(calibrator, win_odds)
        q = cp.q_prime                              # FL-corrected market prob (still NOT model p)
        field_size = field_size if field_size is not None else cp.field_size
    else:
        q = market_implied_win_probs(win_odds)     # raw market vote share (NOT model p)
    jp = joint_probabilities(q, field_size=field_size)  # Feature 009 engine on q (or q')

    def conv(d, rate_key):
        if d is None:
            return None
        r = rates[rate_key]
        return {k: _odds_from_prob(p, r, odds_cap) for k, p in d.items()}

    return EstimatedOdds(
        win=conv(jp.win, "win"),
        place=conv(jp.place, "place"),
        exacta=conv(jp.exacta, "exacta"),
        quinella=conv(jp.quinella, "quinella"),
        wide=conv(jp.wide, "wide"),
        trifecta=conv(jp.trifecta, "trifecta"),
        trio=conv(jp.trio, "trio"),
        payout_rates=dict(rates),
        is_estimated=True,
    )
