"""End-to-end serving: load model -> as-of features -> infer -> consistency -> persist.

Features come from Feature 004 ``build_feature_matrix(end_date=target_date)`` (started
population, leak-safe as-of, NOT build_training_matrix which reads race_results). History is
as-of each row's own race_date (same-day excluded), so result-pending future races are safe.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from horseracing_db.models import Race
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
        predictions, snapshots, explanations = predict_race(model, rid, feature_rows)
        check_consistency(predictions)  # fail-fast (INV-S2); nothing persisted on violation
        run_id = persist_run(
            session,
            race_id=rid,
            model_version=model.model_version,
            logic_version=logic_version,
            feature_version=model.feature_version,
            predictions=predictions,
            snapshots=snapshots,
            explanations=explanations,  # Feature 040
        )
        results.append(
            ServingResult(
                prediction_run_id=run_id, race_id=rid, model_version=model.model_version,
                logic_version=logic_version, n_horses=len(predictions),
            )
        )
    return results
