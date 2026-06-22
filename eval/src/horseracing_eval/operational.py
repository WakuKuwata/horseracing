"""Operational (betting) metrics via single-win simulation (US3, FR-014).

Uses result-time odds -> "疑似評価" (pseudo evaluation), not realized ROI.
Combination bets / estimated odds are deferred to the betting feature.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dataset import EvalRace
from .predictor import Predictor
from .splits import FIRST_VALID_YEAR, expanding_folds


@dataclass(frozen=True)
class BetHorse:
    win_prob: float
    odds: float | None  # result-time single-win odds
    won: bool


@dataclass(frozen=True)
class OperationalMetrics:
    n_races: int
    n_bets: int
    hits: int
    recovery_rate: float       # 回収率 = payout / stake
    pseudo_roi: float          # 疑似ROI = recovery_rate - 1
    hit_rate: float            # 的中率
    skip_rate: float           # 見送り率
    max_drawdown: float        # 最大ドローダウン (金額)
    max_consecutive_losses: int  # 最大連敗数


def simulate_single_win(
    races: list[list[BetHorse]], *, threshold: float = 1.0, stake: float = 100.0
) -> OperationalMetrics:
    """Per race: pick the horse with the highest pseudo-ROI (win_prob*odds); bet if it
    meets ``threshold``, else skip."""
    n_races = len(races)
    n_bets = hits = 0
    stake_total = payout_total = 0.0
    cum = peak = max_dd = 0.0
    cons_loss = max_cons = 0

    for horses in races:
        candidates = [h for h in horses if h.odds is not None and h.odds > 0]
        if not candidates:
            continue
        best = max(candidates, key=lambda h: h.win_prob * h.odds)
        if best.win_prob * best.odds < threshold:
            continue  # 見送り

        n_bets += 1
        stake_total += stake
        if best.won:
            hits += 1
            payout = stake * best.odds
            payout_total += payout
            cum += payout - stake
            cons_loss = 0
        else:
            cum -= stake
            cons_loss += 1
            max_cons = max(max_cons, cons_loss)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    recovery = payout_total / stake_total if stake_total else 0.0
    return OperationalMetrics(
        n_races=n_races,
        n_bets=n_bets,
        hits=hits,
        recovery_rate=recovery,
        pseudo_roi=recovery - 1.0,
        hit_rate=hits / n_bets if n_bets else 0.0,
        skip_rate=(n_races - n_bets) / n_races if n_races else 0.0,
        max_drawdown=max_dd,
        max_consecutive_losses=max_cons,
    )


def simulate_from_predictor(
    predictor: Predictor,
    eval_races: list[EvalRace],
    *,
    first_valid_year: int = FIRST_VALID_YEAR,
    threshold: float = 1.0,
    stake: float = 100.0,
) -> OperationalMetrics:
    """Run the walk-forward folds and simulate single-win bets on the valid races."""
    races: list[list[BetHorse]] = []
    for fold in expanding_folds(eval_races, first_valid_year):
        predictor.fit([er.context for er in fold.train])
        for er in fold.valid:
            preds = predictor.predict_race(er.context)
            winners = {sl.horse_id for sl in er.labels if sl.win == 1}
            bets = []
            for h in er.context.started_horses:
                p = preds.get(h.horse_id)
                if p is None:
                    continue
                odds = h.result_market.odds if h.result_market else None
                bets.append(BetHorse(win_prob=p.win, odds=odds, won=h.horse_id in winners))
            races.append(bets)
    return simulate_single_win(races, threshold=threshold, stake=stake)
