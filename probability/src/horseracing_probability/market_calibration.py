"""Validate the market-odds conversion (contracts/validation.md, R7, FR-009).

The conversion never reads results or model p; results are used only to score q (leak boundary).
Two checks: (a) win-odds recovery — estimated win odds vs actual (exact when payout_rate·Σ1/odds=1),
(b) market-implied q calibration vs the actual winner. All reports are pseudo (estimated market
odds, not real exotic prices).
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Race, RaceHorse, RaceResult
from sqlalchemy import select
from sqlalchemy.orm import Session

from .market_odds import DEFAULT_PAYOUT_RATES, market_implied_win_probs

_EPS = 1e-12


@dataclass(frozen=True)
class RecoveryReport:
    n_races: int
    mean_abs_log_ratio: float   # mean_r |log(R_win · Σ1/odds)| (all horses share this error)
    mean_abs_rel_error: float   # mean over (race,horse) |hat_odds/odds - 1|
    pseudo: bool = True


@dataclass(frozen=True)
class QCalibrationReport:
    n_races: int
    nll: float
    brier: float
    pseudo: bool = True


def recover_win_odds(win_odds: dict[str, float], *, payout_rate_win: float) -> dict[str, float]:
    q = market_implied_win_probs(win_odds)
    return {h: payout_rate_win / qh for h, qh in q.items()}


def _valid(win_odds: dict[str, float]) -> dict[str, float]:
    return {h: float(o) for h, o in win_odds.items() if o is not None and float(o) > 0.0}


def evaluate_from_samples(
    samples: list[tuple[dict[str, float], str | None]], *, payout_rate_win: float
) -> tuple[RecoveryReport, QCalibrationReport]:
    """samples: list of (win_odds, winner_horse_id|None). Pure (no DB)."""
    log_ratios: list[float] = []
    rel_errors: list[float] = []
    q_nll = q_brier = 0.0
    q_races = 0

    for win_odds, winner in samples:
        valid = _valid(win_odds)
        if len(valid) < 2:
            continue
        s = sum(1.0 / o for o in valid.values())
        log_ratios.append(abs(math.log(payout_rate_win * s)))
        hat = recover_win_odds(win_odds, payout_rate_win=payout_rate_win)
        rel_errors.extend(abs(hat[h] / valid[h] - 1.0) for h in valid)

        if winner is not None and winner in valid:
            q = market_implied_win_probs(win_odds)
            qw = q[winner]
            q_nll += -math.log(max(qw, _EPS))
            q_brier += 1.0 - 2.0 * qw + sum(v * v for v in q.values())
            q_races += 1

    n = max(len(log_ratios), 1)
    recovery = RecoveryReport(
        n_races=len(log_ratios),
        mean_abs_log_ratio=sum(log_ratios) / n,
        mean_abs_rel_error=(sum(rel_errors) / len(rel_errors)) if rel_errors else 0.0,
    )
    qm = max(q_races, 1)
    qcal = QCalibrationReport(n_races=q_races, nll=q_nll / qm, brier=q_brier / qm)
    return recovery, qcal


# --- DB wrapper -------------------------------------------------------------
def _race_winodds_and_winner(session: Session, race_id: str) -> tuple[dict[str, float], str | None]:
    win_odds = {
        hid: float(o)
        for hid, o in session.execute(
            select(RaceHorse.horse_id, RaceHorse.odds)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        ).all()
        if o is not None and float(o) > 0.0
    }
    winners = list(
        session.scalars(
            select(RaceResult.horse_id)
            .where(RaceResult.race_id == race_id)
            .where(RaceResult.result_status == ResultStatus.FINISHED)
            .where(RaceResult.finish_order == 1)
        )
    )
    winner = winners[0] if len(winners) == 1 else None  # dead heat -> no single winner
    return win_odds, winner


def evaluate_market_odds(
    session: Session, *, start_date: datetime.date, end_date: datetime.date,
    payout_rates: dict[str, float] | None = None,
) -> tuple[RecoveryReport, QCalibrationReport]:
    rates = {**DEFAULT_PAYOUT_RATES, **(payout_rates or {})}
    race_ids = list(
        session.scalars(
            select(Race.race_id)
            .where(Race.race_date >= start_date)
            .where(Race.race_date <= end_date)
            .order_by(Race.race_id)
        )
    )
    samples = [_race_winodds_and_winner(session, rid) for rid in race_ids]
    return evaluate_from_samples(samples, payout_rate_win=rates["win"])
