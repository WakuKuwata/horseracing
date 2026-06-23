"""Pseudo-ROI scoring (contracts/backtest.md, R3, FR-007/009).

hit = winner (result_status='finished' AND finish_order==1); payout = stake×odds, else 0.
DNF (started, not 1st) is a loss; scratched horses never enter (not in the started population).
Recovery rate and hit rate are bet-level; max_drawdown (absolute, stake units) and
max_losing_streak are RACE-level over bet races only (a losing race = race_pnl < 0). skip_rate =
no-bet races / evaluated races. All reports are pseudo (settled odds).
"""

from __future__ import annotations

from dataclasses import dataclass

from .strategies import Strategy


@dataclass(frozen=True)
class RaceOutcome:
    race_id: str
    horses: list[dict]      # strategy input: horse_id, horse_number, win_prob, odds, entry_status
    winners: set[str]       # horse_ids finished 1st (dead-heat -> multiple)


@dataclass(frozen=True)
class RoiReport:
    strategy: str
    n_races: int            # evaluated races
    n_bet_races: int
    n_bets: int
    total_stake: float
    total_payout: float
    recovery_rate: float    # Σpayout / Σstake (pseudo)
    hit_rate: float         # hits / n_bets
    skip_rate: float        # no-bet races / n_races
    max_drawdown: float     # absolute, bet races only (max peak-to-trough of Σ race_pnl)
    max_losing_streak: int  # consecutive bet races with race_pnl < 0
    in_sample: bool = False
    pseudo: bool = True


def score_backtest(
    race_outcomes: list[RaceOutcome], strategy: Strategy, *, stake: float, in_sample: bool = False
) -> RoiReport:
    n_bet_races = n_bets = hits = 0
    total_stake = total_payout = 0.0
    cum = peak = max_dd = 0.0
    streak = max_streak = 0

    for ro in race_outcomes:
        bets = strategy.bets_for_race(ro.horses, stake=stake)
        if not bets:
            continue
        n_bet_races += 1
        race_pnl = 0.0
        for b in bets:
            n_bets += 1
            total_stake += b.stake
            won = b.horse_id in ro.winners
            payout = b.stake * b.odds if won else 0.0
            total_payout += payout
            hits += 1 if won else 0
            race_pnl += payout - b.stake
        cum += race_pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if race_pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    n_races = len(race_outcomes)
    return RoiReport(
        strategy=strategy.name,
        n_races=n_races,
        n_bet_races=n_bet_races,
        n_bets=n_bets,
        total_stake=total_stake,
        total_payout=total_payout,
        recovery_rate=(total_payout / total_stake) if total_stake > 0 else 0.0,
        hit_rate=(hits / n_bets) if n_bets > 0 else 0.0,
        skip_rate=((n_races - n_bet_races) / n_races) if n_races > 0 else 0.0,
        max_drawdown=max_dd,
        max_losing_streak=max_streak,
        in_sample=in_sample,
        pseudo=True,
    )
