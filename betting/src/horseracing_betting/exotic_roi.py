"""Per-bet-type pseudo-ROI scoring + aggregation (research.md R3/R4, contracts/exotic_backtest.md).

hit matching is per bet type (exacta/trifecta=ordered, quinella/trio=set, wide/place=inclusion with
the 009 field rule on the CANONICAL field_size). place/wide multiple hits are scored at BET LEVEL —
each in-range selection pays independently (never capped per race). payout = stake × O_est (DOUBLE-
pseudo: estimated odds + PL extrapolation). is_hit None (dead-heat ambiguous for ordered/set bets)
drops the bet from scoring (audited as a skip). All reports carry pseudo=True.
"""

from __future__ import annotations

from collections.abc import Iterable

from .exotic_selection import is_hit
from .exotic_types import ExoticBet, ExoticRaceOutcome, ExoticRoiReport, ScoredBet

TOTAL = "__total__"


def score_exotic(
    bets: Iterable[ExoticBet],
    outcome: ExoticRaceOutcome,
    *,
    stake: float,
    real_odds: dict[tuple[str, tuple[int, ...]], float] | None = None,
    scratched: set[int] | None = None,
) -> tuple[list[ScoredBet], int]:
    """Score each bet; returns (scored, n_skipped).

    payout uses the REAL dividend when ``real_odds`` has the selection (pseudo=False, real ROI),
    else the estimated O_est (pseudo=True, double-pseudo). A bet whose selection includes a
    post-recommendation ``scratched`` horse is VOIDED (skipped, no payout). Dead-heat-ambiguous
    ordered/set bets (is_hit None) are also skipped (audit).
    """
    real_odds = real_odds or {}
    scratched = scratched or set()
    scored: list[ScoredBet] = []
    skipped = 0
    for b in bets:
        if any(n in scratched for n in b.selection):
            skipped += 1  # post-recommendation scratch -> void (do not pay, do not estimate)
            continue
        hit = is_hit(b.bet_type, b.selection, outcome.finish_pos, field_size=outcome.field_size)
        if hit is None:
            skipped += 1  # ordered/set bet not scoreable (dead-heat) -> audit skip
            continue
        real = real_odds.get((b.bet_type, tuple(b.selection)))
        odds_used, pseudo = (b.o_est, True) if real is None else (real, False)
        payout = stake * odds_used if hit else 0.0
        scored.append(ScoredBet(bet=b, stake=stake, hit=bool(hit), payout=payout, pseudo=pseudo))
    return scored, skipped


def _metrics(
    strategy: str, bet_type: str, scored: list[ScoredBet], *, opportunities: int, skipped: int
) -> ExoticRoiReport:
    n_bets = len(scored)
    n_hits = sum(1 for s in scored if s.hit)
    total_stake = sum(s.stake for s in scored)
    total_payout = sum(s.payout for s in scored)

    cum = peak = max_dd = 0.0
    streak = max_streak = 0
    for s in scored:  # bet-level sequence
        cum += s.profit
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if s.profit < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return ExoticRoiReport(
        strategy=strategy,
        bet_type=bet_type,
        n_bets=n_bets,
        n_hits=n_hits,
        hit_rate=(n_hits / n_bets) if n_bets else 0.0,
        total_stake=total_stake,
        total_payout=total_payout,
        roi=(total_payout / total_stake) if total_stake > 0 else 0.0,
        skip_rate=(skipped / opportunities) if opportunities else 0.0,
        max_drawdown=max_dd,
        max_consecutive_losses=max_streak,
        # real ROI only when EVERY payout used a real dividend; any estimated => double-pseudo label
        pseudo=any(s.pseudo for s in scored) if scored else True,
    )


def aggregate_roi(
    scored: list[ScoredBet],
    *,
    strategy: str,
    opportunities: dict[str, int] | None = None,
    skipped: dict[str, int] | None = None,
) -> dict[str, ExoticRoiReport]:
    """Per-bet-type + ``__total__`` reports. skip_rate uses opportunity/skip counts per bet type."""
    opportunities = opportunities or {}
    skipped = skipped or {}
    by_type: dict[str, list[ScoredBet]] = {}
    for s in scored:
        by_type.setdefault(s.bet.bet_type, []).append(s)

    reports: dict[str, ExoticRoiReport] = {}
    for bt, subset in by_type.items():
        reports[bt] = _metrics(
            strategy, bt, subset,
            opportunities=opportunities.get(bt, 0), skipped=skipped.get(bt, 0),
        )
    reports[TOTAL] = _metrics(
        strategy, TOTAL, scored,
        opportunities=sum(opportunities.values()), skipped=sum(skipped.values()),
    )
    return reports
