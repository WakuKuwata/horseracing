"""Calibration of joint probabilities vs the (wrong) independent-product baseline (R5, FR-009).

Probability derivation never reads results; results are used only to score the realized ordered
combination (leak boundary). The naive baseline (exacta ∝ p_i·p_j, trifecta ∝ p_i·p_j·p_k,
renormalized) ignores sampling-without-replacement — Plackett-Luce should not be worse.
"""

from __future__ import annotations

import datetime
import itertools
import math
from dataclasses import dataclass

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import PredictionRun, RaceHorse, RacePrediction, RaceResult
from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine import _normalize_clip, joint_probabilities

_EPS = 1e-12
_K = {"exacta": 2, "trifecta": 3}


@dataclass(frozen=True)
class CalibrationReport:
    strategy: str
    bet_type: str
    n_races: int
    nll: float
    brier: float


def independent_product(win_probs: dict[str, float], k: int) -> dict[tuple[str, ...], float]:
    """Naive baseline: ordered k-combination probability ∝ Π p, renormalized to Σ=1."""
    ids, p = _normalize_clip(win_probs, 1e-9)
    prob = dict(zip(ids, p, strict=True))
    raw = {
        combo: math.prod(prob[h] for h in combo)
        for combo in itertools.permutations(ids, k)
    }
    z = sum(raw.values())
    return {c: v / z for c, v in raw.items()} if z > 0 else raw


def _pl_dist(win_probs: dict[str, float], bet_type: str) -> dict[tuple[str, ...], float]:
    jp = joint_probabilities(win_probs)
    return dict(jp.exacta) if bet_type == "exacta" else dict(jp.trifecta)


def _score(dist: dict[tuple[str, ...], float], realized: tuple[str, ...]) -> tuple[float, float]:
    p_real = dist.get(realized, _EPS)
    nll = -math.log(max(p_real, _EPS))
    sum_sq = sum(v * v for v in dist.values())
    brier = 1.0 - 2.0 * p_real + sum_sq   # full multiclass Brier
    return nll, brier


def calibrate(
    samples: list[tuple[dict[str, float], tuple[str, ...]]], *, bet_type: str
) -> dict[str, CalibrationReport]:
    """samples: list of (running-field win_probs, realized ordered combo). Pure (no DB)."""
    k = _K[bet_type]
    strategies = {
        "plackett_luce": lambda wp: _pl_dist(wp, bet_type),
        "independent_product": lambda wp: independent_product(wp, k),
    }
    out: dict[str, CalibrationReport] = {}
    for name, dist_fn in strategies.items():
        nll = brier = 0.0
        for win_probs, realized in samples:
            n, b = _score(dist_fn(win_probs), realized)
            nll += n
            brier += b
        m = max(len(samples), 1)
        out[name] = CalibrationReport(name, bet_type, len(samples), nll / m, brier / m)
    return out


# --- DB wrapper -------------------------------------------------------------
def _latest_run_predictions(session: Session, race_id: str) -> dict[str, float]:
    run = session.scalars(
        select(PredictionRun)
        .where(PredictionRun.race_id == race_id)
        .order_by(PredictionRun.computed_at.desc())
    ).first()
    if run is None:
        return {}
    rows = session.execute(
        select(RacePrediction.horse_id, RacePrediction.win_prob).where(
            RacePrediction.prediction_run_id == run.prediction_run_id
        )
    ).all()
    started = set(
        session.scalars(
            select(RaceHorse.horse_id)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        )
    )
    return {hid: float(wp) for hid, wp in rows if wp is not None and hid in started}


def _realized_combo(session: Session, race_id: str, k: int) -> tuple[str, ...] | None:
    rows = session.execute(
        select(RaceResult.horse_id, RaceResult.finish_order)
        .where(RaceResult.race_id == race_id)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
    ).all()
    by_pos: dict[int, list[str]] = {}
    for hid, fo in rows:
        if fo is not None:
            by_pos.setdefault(int(fo), []).append(hid)
    combo: list[str] = []
    for pos in range(1, k + 1):
        horses = by_pos.get(pos)
        if not horses or len(horses) != 1:  # missing or dead-heat -> not a unique ordered combo
            return None
        combo.append(horses[0])
    return tuple(combo)


def evaluate_calibration(
    session: Session, *, start_date: datetime.date, end_date: datetime.date, bet_type: str
) -> dict[str, CalibrationReport]:
    from horseracing_db.models import Race

    k = _K[bet_type]
    race_ids = list(
        session.scalars(
            select(Race.race_id)
            .where(Race.race_date >= start_date)
            .where(Race.race_date <= end_date)
            .order_by(Race.race_id)
        )
    )
    samples: list[tuple[dict[str, float], tuple[str, ...]]] = []
    for rid in race_ids:
        win_probs = _latest_run_predictions(session, rid)
        if len(win_probs) < k:
            continue
        realized = _realized_combo(session, rid, k)
        if realized is None or any(h not in win_probs for h in realized):
            continue
        samples.append((win_probs, realized))
    return calibrate(samples, bet_type=bet_type)
