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


@dataclass(frozen=True)
class QvsQpReport:
    """FL correction win-rate calibration: raw q vs corrected q' (Feature 013, adoption gate)."""

    scope: str                 # "overall" or a popularity-band label
    n_races: int
    n_samples: int
    nll_q: float
    brier_q: float
    ece_q: float
    nll_qp: float
    brier_qp: float
    ece_qp: float
    reliability_q: list         # list of (mean_pred, empirical_rate, n) per fixed bin
    reliability_qp: list
    improved: bool              # q' beats q on NLL (adoption signal)
    pseudo: bool = True


@dataclass(frozen=True)
class DivergenceDeltaReport:
    """Estimated-vs-real exotic divergence before/after FL correction (013, DIAGNOSTIC only)."""

    bet_type: str
    coverage_rate: float
    logratio_median_q: float
    logratio_mae_q: float
    logratio_p90_q: float
    logratio_median_qp: float
    logratio_mae_qp: float
    logratio_p90_qp: float
    baseline: str = "estimated raw q"
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


#: fixed default reliability/ECE bin edges (deterministic, call-site independent).
DEFAULT_BINS: tuple[float, ...] = tuple(i / 10.0 for i in range(11))  # 10 equal-width [0,0.1,…,1.0]


def _reliability_and_ece(pred_winner_pairs, bins):
    """pred_winner_pairs: list of (prob_of_actual_winner, n_field). Returns (reliability, ece).

    For each fixed bin we accumulate the winner-probability mass vs the empirical hit rate. A
    horse-level reliability would need every horse's prob; here we score the WINNER's predicted
    prob against the realized outcome (=1), which is the calibration signal for the win market.
    """
    # bins over predicted winner prob; empirical rate = fraction that actually won (always 1 here),
    # so we instead bin ALL horses' predicted probs vs their realized win indicator.
    nb = len(bins) - 1
    sum_pred = [0.0] * nb
    sum_obs = [0.0] * nb
    cnt = [0] * nb
    for prob, won in pred_winner_pairs:
        b = min(nb - 1, max(0, int(prob * nb)))
        if prob >= 1.0:
            b = nb - 1
        sum_pred[b] += prob
        sum_obs[b] += 1.0 if won else 0.0
        cnt[b] += 1
    reliability = []
    total = sum(cnt)
    ece = 0.0
    for b in range(nb):
        if cnt[b] == 0:
            reliability.append((0.0, 0.0, 0))
            continue
        mp = sum_pred[b] / cnt[b]
        er = sum_obs[b] / cnt[b]
        reliability.append((mp, er, cnt[b]))
        ece += (cnt[b] / total) * abs(mp - er) if total else 0.0
    return reliability, ece


def evaluate_q_vs_qprime(
    samples, calibrator, *, bins: tuple[float, ...] = DEFAULT_BINS
) -> QvsQpReport:
    """Win-rate calibration of raw q vs FL-corrected q' on the ACTUAL winner (013 adoption gate).

    Per race, score every horse's predicted win prob (q and q') against its realized win indicator;
    NLL/Brier use the winner. ECE/reliability use the fixed ``bins`` on the normalized q'. Dead
    heats / no-winner races are excluded (counted). q' beats q => ``improved``. All pseudo.
    """
    from .fl_bias import apply_g

    gamma = calibrator.params["gamma"]
    nll_q = brier_q = nll_qp = brier_qp = 0.0
    n = 0
    pairs_q: list[tuple[float, bool]] = []
    pairs_qp: list[tuple[float, bool]] = []
    for win_odds, winner in samples:
        valid = _valid(win_odds)
        if len(valid) < 2 or winner is None or winner not in valid:
            continue
        q = market_implied_win_probs(valid)
        qp = apply_g(calibrator.method, {"gamma": gamma}, q)
        n += 1
        nll_q += -math.log(max(q[winner], _EPS))
        nll_qp += -math.log(max(qp[winner], _EPS))
        brier_q += 1.0 - 2.0 * q[winner] + sum(v * v for v in q.values())
        brier_qp += 1.0 - 2.0 * qp[winner] + sum(v * v for v in qp.values())
        for h in valid:
            pairs_q.append((q[h], h == winner))
            pairs_qp.append((qp[h], h == winner))
    if n == 0:
        raise ValueError("no informative races for q-vs-q' evaluation (insufficient data)")
    rel_q, ece_q = _reliability_and_ece(pairs_q, bins)
    rel_qp, ece_qp = _reliability_and_ece(pairs_qp, bins)
    return QvsQpReport(
        scope="overall", n_races=n, n_samples=n,
        nll_q=nll_q / n, brier_q=brier_q / n, ece_q=ece_q,
        nll_qp=nll_qp / n, brier_qp=brier_qp / n, ece_qp=ece_qp,
        reliability_q=rel_q, reliability_qp=rel_qp,
        improved=(nll_qp < nll_q),  # adoption gate: lower winner NLL on the normalized q'
    )


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
