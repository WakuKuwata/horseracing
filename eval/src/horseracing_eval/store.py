"""Persist baseline evaluation results to model_versions.metrics_summary (FR-012)."""

from __future__ import annotations

from horseracing_db.models import ModelVersion
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .harness import EvalResult


def save_baseline(
    session: Session,
    model_version: str,
    result: EvalResult,
    *,
    model_family: str = "baseline",
) -> None:
    """Upsert a model_versions row holding the baseline's metrics_summary."""
    summary = result.to_summary()
    stmt = insert(ModelVersion).values(
        model_version=model_version,
        model_family=model_family,
        label_schema="win_top2_top3",
        adoption_status="candidate",
        metrics_summary=summary,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["model_version"],
        set_={"metrics_summary": summary, "model_family": model_family},
    )
    session.execute(stmt)
    session.commit()
