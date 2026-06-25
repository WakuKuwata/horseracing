"""Deterministic prediction-run selection + canonical win-prob population (Feature 014).

A race may have several prediction_runs across model versions. We pick deterministically: the run
whose model is adopted (``adoption_status='active'``) first, then most recent ``computed_at``, then
highest ``prediction_run_id`` — a total order. PredictionRun has no adoption_status column, so we
JOIN model_versions. The chosen run_id is returned to the caller for the audit envelope. Canonical
win probs exclude scratched/non-starters and non-positive probs (constitution IV) for 009.
"""

from __future__ import annotations

from horseracing_db.enums import AdoptionStatus, EntryStatus
from horseracing_db.models import ModelVersion, PredictionRun, RaceHorse, RacePrediction
from sqlalchemy import case, select
from sqlalchemy.orm import Session


def select_prediction_run(session: Session, race_id: str) -> PredictionRun | None:
    """Deterministic latest run: active model → computed_at DESC → prediction_run_id DESC."""
    active_first = case((ModelVersion.adoption_status == AdoptionStatus.ACTIVE, 0), else_=1)
    return session.scalars(
        select(PredictionRun)
        .join(ModelVersion, PredictionRun.model_version == ModelVersion.model_version)
        .where(PredictionRun.race_id == race_id)
        .order_by(
            active_first,
            PredictionRun.computed_at.desc(),
            PredictionRun.prediction_run_id.desc(),
        )
    ).first()


def canonical_win_probs(session: Session, *, run_id, race_id: str) -> dict[int, float]:
    """{horse_number -> win_prob} for STARTED horses with positive win_prob (009 input pop).

    Scratched/excluded horses and non-positive/None probs are dropped; the 009 engine renormalizes.
    """
    rows = session.execute(
        select(RaceHorse.horse_number, RacePrediction.win_prob)
        .join(RacePrediction, RacePrediction.horse_id == RaceHorse.horse_id)
        .where(RaceHorse.race_id == race_id)
        .where(RacePrediction.prediction_run_id == run_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    out: dict[int, float] = {}
    for horse_number, win_prob in rows:
        if horse_number is None or win_prob is None or float(win_prob) <= 0.0:
            continue
        out[int(horse_number)] = float(win_prob)
    return out
