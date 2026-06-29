"""Enqueue refresh jobs into ingestion_jobs (durable queue) with race-level dedup (Feature 024).

dedup (data-model D3): under a per-race advisory lock, reuse an active (queued/running) job, else
(unless force) reuse a recently-succeeded job within the freshness window, else INSERT a new queued
job. The advisory lock is transaction-scoped (released on the request's commit), so two concurrent
enqueues for the same race cannot both INSERT.
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob, Race
from horseracing_db.validation import is_valid_race_id
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import JOB_TYPE_DAY, JOB_TYPE_PREDICT, JOB_TYPE_RACE
from .config import CONFIG

#: default freshness window — a same-race success within this many seconds is reused (US3/FR-015).
DEFAULT_FRESH_SECONDS = CONFIG.fresh_seconds

_ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def race_exists(session: Session, race_id: str) -> bool:
    return session.get(Race, race_id) is not None


def _lock_race(session: Session, race_id: str) -> None:
    # transaction-scoped advisory lock keyed on the race (released at commit); serialises enqueue.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"refresh:race:{race_id}"})


def enqueue_race(
    session: Session,
    race_id: str,
    *,
    force: bool = False,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
    trace_id: str | None = None,
) -> tuple[IngestionJob, bool]:
    """Return (job, reused). Caller commits (releasing the advisory lock)."""
    _lock_race(session, race_id)

    active = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_RACE)
        .where(IngestionJob.scope_value == race_id)
        .where(IngestionJob.status.in_(_ACTIVE))
        .order_by(IngestionJob.created_at.desc())
    ).first()
    if active is not None:
        return active, True

    if not force:
        cutoff = _now() - datetime.timedelta(seconds=fresh_seconds)
        fresh = session.scalars(
            select(IngestionJob)
            .where(IngestionJob.job_type == JOB_TYPE_RACE)
            .where(IngestionJob.scope_value == race_id)
            .where(IngestionJob.status == JobStatus.SUCCEEDED)
            .where(IngestionJob.completed_at.is_not(None))
            .where(IngestionJob.completed_at >= cutoff)
            .order_by(IngestionJob.completed_at.desc())
        ).first()
        if fresh is not None:
            return fresh, True

    job = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_RACE, scope="race", scope_value=race_id,
        status=JobStatus.QUEUED, trace_id=trace_id,
    )
    session.add(job)
    session.flush()
    return job, False


def enqueue_predict(session: Session, race_id: str) -> tuple[IngestionJob, bool]:
    """Feature 028: enqueue a predict job (in-flight-only dedup). (job, reused); caller commits.

    Reuse only an ACTIVE (queued/running) predict job for the same race — so a double-click can't
    create two. A completed job is NOT reused (an explicit click means "(re)generate now", e.g.
    after the model or entries changed). The advisory lock key is `predict:{race_id}` (distinct from
    refresh's `refresh:race:{race_id}`), so predict and refresh never block each other.
    model_version is not in the dedup key (ingestion_jobs has no payload column) — it is recorded in
    prediction_runs for audit instead.
    """
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"predict:{race_id}"})
    active = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_PREDICT)
        .where(IngestionJob.scope_value == race_id)
        .where(IngestionJob.status.in_(_ACTIVE))
        .order_by(IngestionJob.created_at.desc())
    ).first()
    if active is not None:
        return active, True

    job = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_PREDICT, scope="race", scope_value=race_id,
        status=JobStatus.QUEUED, summary={"kind": "predict", "source": "manual"},
    )
    session.add(job)
    session.flush()
    return job, False


def list_race_ids_for_day(session: Session, date: datetime.date) -> list[str]:
    """All valid 12-digit race_ids on a date (pending and finished), ordered for stable batches."""
    rows = session.scalars(
        select(Race.race_id).where(Race.race_date == date).order_by(Race.race_id.asc())
    ).all()
    return [r for r in rows if is_valid_race_id(r)]


def enqueue_day_parent(session: Session, date: datetime.date) -> IngestionJob:
    """Create just the parent refresh_day job (QUEUED) and return it; the worker discovers the
    day's races from netkeiba and fans out refresh_race children (so the POST returns 202 without a
    netkeiba round-trip). Accepts any date — even one with no DB races yet (worker discovers)."""
    parent = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_DAY, scope="day",
        scope_value=date.isoformat(), status=JobStatus.QUEUED,
    )
    session.add(parent)
    session.flush()
    parent.trace_id = str(parent.ingestion_job_id)
    return parent


def enqueue_day(
    session: Session, date: datetime.date, *, force: bool = False,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
) -> tuple[IngestionJob, list[tuple[IngestionJob, bool]]]:
    """Create a parent refresh_day job + one refresh_race child per race, sharing a trace_id.

    Returns (parent_job, [(child_job, reused), ...]). Caller commits.
    """
    race_ids = list_race_ids_for_day(session, date)
    parent = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_DAY, scope="day",
        scope_value=date.isoformat(), status=JobStatus.QUEUED,
    )
    session.add(parent)
    session.flush()
    trace_id = str(parent.ingestion_job_id)
    parent.trace_id = trace_id

    children: list[tuple[IngestionJob, bool]] = []
    for rid in race_ids:
        children.append(
            enqueue_race(session, rid, force=force, fresh_seconds=fresh_seconds, trace_id=trace_id)
        )
    return parent, children


def batch_status(children_statuses: list[str]) -> str:
    """Aggregate a batch's status from its children (data-model)."""
    if not children_statuses:
        return JobStatus.SUCCEEDED
    if any(s in _ACTIVE for s in children_statuses):
        return JobStatus.RUNNING
    if all(s == JobStatus.SUCCEEDED for s in children_statuses):
        return JobStatus.SUCCEEDED
    return JobStatus.PARTIAL


def count_by(session: Session, trace_id: str) -> dict[str, int]:
    rows = session.execute(
        select(IngestionJob.status, func.count())
        .where(IngestionJob.trace_id == trace_id)
        .where(IngestionJob.job_type == JOB_TYPE_RACE)
        .group_by(IngestionJob.status)
    ).all()
    return {status: int(c) for status, c in rows}
