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


#: Stage discount for MARKET-derived combination prices, fitted DIRECTLY against real exotic
#: price grids (1,001 races / 593,740 combinations of 馬連・ワイド・三連複 from netkeiba's odds API,
#: 2026-07-29; fitted on 667 races, reported on 334 held out).
#:
#: Plain Harville (λ=1) overstates how often a favourite fills the minor placings, and that error
#: compounds into every derived combination price — increasingly so toward the long shots. Held-out
#: median of real / estimated price, by real-odds band:
#:
#:   λ=1        馬連 1.10 → 0.49   ワイド 1.02 → 0.19   三連複 1.31 → 0.76   (~10x → 1000x+)
#:   THESE λ    馬連 0.93 → 0.85   ワイド 0.90 → 0.70   三連複 0.92 → 1.24
#:
#: overall medians 0.73/0.63/0.73 → 0.98/0.94/1.11 and log-error 0.64/0.86/0.82 → 0.45/0.43/0.64.
#:
#: NOT the same quantity as two other λ pairs in this repo, and they must never be substituted for
#: each other: 049's 0.852/0.707 was fitted on MODEL p for the displayed top2/top3 probabilities,
#: and 084's 0.8312/0.7101 was fitted on market q against the top-3 popularity composition (that
#: one lives in the chaos artifact JSON, not here). 084's pair was the previous default and is
#: still a good estimate — this fit only moved the band profile from a 0.98→0.70 tilt to
#: 0.93→0.85 — but it was fitted for a different target, and the exotic grids are the direct
#: evidence for exotic prices.
#:
#: Known residual: every bet type still under-prices the 1000x+ band (0.70–0.85), and 三連複 runs
#: ~10% long overall. A single λ pair cannot fix both ends — pushing λ lower flattens the long
#: shots by tilting the favourites the other way (measured: λ=0.65 gives 馬連 0.71 → 1.11). λ is
#: per-PLACING-STAGE and shared across bet types by construction, so it cannot be split per bet
#: type without breaking the engine's identities (wide{i,j} = Σ_k trio{i,j,k}, and quinella = both
#: exacta orderings).
MARKET_STAGE_LAMBDA2 = 0.75
MARKET_STAGE_LAMBDA3 = 0.70


def default_market_stage_discount():
    """The stage discount for market-derived combination prices (see the constants above).

    ``StageDiscount`` lives in ``horseracing_eval`` (049 put the derivation there because the fit
    needs the evaluation harness), and ``probability`` must not hard-depend on it — the dependency
    runs probability -> eval, not back. Callers that already import eval can pass their own.
    """
    from horseracing_eval.stage_discount import StageDiscount  # lazy: keep the dep one-way
    return StageDiscount(lambda2=MARKET_STAGE_LAMBDA2, lambda3=MARKET_STAGE_LAMBDA3)


def estimate_market_odds(
    win_odds: dict[str, float],
    *,
    field_size: int | None = None,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = DEFAULT_ODDS_CAP,
    calibrator=None,
    stage_discount=None,
) -> EstimatedOdds:
    """Estimated exotic odds from WIN odds. With ``calibrator`` (Feature 013), the market vote
    share q is FL-bias-corrected to q' before the 009 engine; without it, raw q (backward
    compatible). q'/q are market-derived, never the model p (p≠q).

    ``stage_discount`` (Feature 049) applies the Benter p^λ weights to the 2nd/3rd placing stages.
    Plain Harville (the default, λ=1) systematically overstates how often a favourite fills the
    minor placings, which inflates every derived combination price: measured against the real
    exotic grids the λ=1 estimate sits at ~0.75 (quinella) / ~0.67 (wide) of the market's own
    price, and the gap widens monotonically toward the long shots. 049 already fitted λ for the
    displayed top2/top3 probabilities and cut their calibration error ~5x; this parameter is what
    lets the same correction reach the estimated odds. None keeps the legacy path byte-identical.
    """
    rates = {**DEFAULT_PAYOUT_RATES, **(payout_rates or {})}
    if calibrator is not None:
        from .fl_bias import apply_calibrator  # lazy: avoid import cycle
        cp = apply_calibrator(calibrator, win_odds)
        q = cp.q_prime                              # FL-corrected market prob (still NOT model p)
        field_size = field_size if field_size is not None else cp.field_size
    else:
        q = market_implied_win_probs(win_odds)     # raw market vote share (NOT model p)
    # Feature 009 engine on q (or q'); stage_discount=None reproduces the legacy arithmetic exactly
    jp = joint_probabilities(q, field_size=field_size, stage_discount=stage_discount)

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
