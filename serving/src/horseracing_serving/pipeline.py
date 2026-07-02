"""End-to-end serving: load model -> as-of features -> infer -> consistency -> persist.

Features come from Feature 004 ``build_feature_matrix(end_date=target_date)`` (started
population, leak-safe as-of, NOT build_training_matrix which reads race_results). History is
as-of each row's own race_date (same-day excluded), so result-pending future races are safe.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from horseracing_db.models import PredictionRun, Race
from horseracing_eval.consistency import check_consistency
from horseracing_features.builder import build_feature_matrix
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import SERVING_LOGIC_VERSION
from .model_loader import ServingError, load_serving_model
from .persistence import persist_run
from .predictor import predict_race


@dataclass(frozen=True)
class ServingResult:
    prediction_run_id: object
    race_id: str
    model_version: str
    logic_version: str
    n_horses: int


def _targets(
    session: Session, race_id: str | None, date: datetime.date | None
) -> tuple[datetime.date, list[str]]:
    if race_id is not None:
        race = session.get(Race, race_id)
        if race is None or race.race_date is None:
            raise ServingError(f"race {race_id} not found or has no race_date")
        return race.race_date, [race_id]
    if date is not None:
        stmt = select(Race.race_id).where(Race.race_date == date).order_by(Race.race_id)
        race_ids = list(session.scalars(stmt))
        if not race_ids:
            raise ServingError(f"no races on {date.isoformat()}")
        return date, race_ids
    raise ServingError("either race_id or date is required")


def run_serving(
    session: Session,
    *,
    race_id: str | None = None,
    date: datetime.date | None = None,
    model_version: str | None = None,
) -> list[ServingResult]:
    model = load_serving_model(session, model_version)
    target_date, race_ids = _targets(session, race_id, date)
    logic_version = f"feat={model.feature_version};serve={SERVING_LOGIC_VERSION}"

    feature_rows = build_feature_matrix(session, end_date=target_date)
    present = set(feature_rows["race_id"].unique())

    results: list[ServingResult] = []
    for rid in race_ids:
        if rid not in present:  # no started horses / out of feature scope
            continue
        results.append(_predict_persist(session, model, rid, feature_rows, logic_version))
    return results


def _predict_persist(
    session: Session, model, race_id: str, feature_rows, logic_version: str
) -> ServingResult:
    """Predict one race + persist the run (shared by run_serving and run_serving_backfill).

    Identical per-race path so backfill predictions are byte-identical to run_serving (p-parity).
    """
    predictions, snapshots, explanations = predict_race(model, race_id, feature_rows)
    check_consistency(predictions)  # fail-fast (INV-S2); nothing persisted on violation
    run_id = persist_run(
        session,
        race_id=race_id,
        model_version=model.model_version,
        logic_version=logic_version,
        feature_version=model.feature_version,
        predictions=predictions,
        snapshots=snapshots,
        explanations=explanations,  # Feature 040
    )
    return ServingResult(
        prediction_run_id=run_id, race_id=race_id, model_version=model.model_version,
        logic_version=logic_version, n_horses=len(predictions),
    )


@dataclass(frozen=True)
class BackfillCounts:
    generated: int = 0
    skip_exists: int = 0      # target model already has a run for the race (idempotent)
    skip_no_started: int = 0  # no started horses / out of feature scope
    error_days: int = 0       # days whose processing raised (isolated, not aborting)

    def as_dict(self) -> dict:
        return {
            "generated": self.generated, "skip_exists": self.skip_exists,
            "skip_no_started": self.skip_no_started, "error_days": self.error_days,
        }


def run_serving_backfill(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    model_version: str | None = None,
    force: bool = False,
) -> BackfillCounts:
    """Feature 044: generate predictions over a date range for the (single active) model.

    Per-DAY it rebuilds the feature matrix (end_date=day) and predicts via the SAME _predict_persist
    path as run_serving → p-parity. Idempotent: a race that already has a prediction_run for the
    resolved model_version is skipped (``force`` regenerates, append-only). Per-day exception
    isolation (one bad day doesn't abort the range). Returns reconciliation counts.
    """
    model = load_serving_model(session, model_version)
    logic_version = f"feat={model.feature_version};serve={SERVING_LOGIC_VERSION}"
    gen = skip_exists = skip_no_started = error_days = 0

    day = date_from
    while day <= date_to:
        try:
            race_ids = list(
                session.scalars(
                    select(Race.race_id).where(Race.race_date == day).order_by(Race.race_id)
                )
            )
            if race_ids:
                feature_rows = build_feature_matrix(session, end_date=day)
                present = set(feature_rows["race_id"].unique())
                for rid in race_ids:
                    if rid not in present:
                        skip_no_started += 1
                        continue
                    if not force and _has_run_for_model(session, rid, model.model_version):
                        skip_exists += 1
                        continue
                    _predict_persist(session, model, rid, feature_rows, logic_version)
                    gen += 1
        except Exception:  # noqa: BLE001 — one day must not abort the whole range
            session.rollback()
            error_days += 1
        day += datetime.timedelta(days=1)

    return BackfillCounts(
        generated=gen, skip_exists=skip_exists,
        skip_no_started=skip_no_started, error_days=error_days,
    )


def _has_run_for_model(session: Session, race_id: str, model_version: str) -> bool:
    """True if the race already has a prediction_run for this model_version (idempotency)."""
    return session.scalars(
        select(PredictionRun.prediction_run_id)
        .where(PredictionRun.race_id == race_id)
        .where(PredictionRun.model_version == model_version)
    ).first() is not None
