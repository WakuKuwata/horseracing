"""Enqueue refresh jobs into ingestion_jobs (durable queue) with race-level dedup (Feature 024).

dedup (data-model D3): under a per-race advisory lock, reuse an active (queued/running) job, else
(unless force) reuse a recently-succeeded job within the freshness window, else INSERT a new queued
job. The advisory lock is transaction-scoped (released on the request's commit), so two concurrent
enqueues for the same race cannot both INSERT.
"""

from __future__ import annotations

import datetime
from typing import Literal

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob, Race, RaceResult
from horseracing_db.validation import is_valid_race_id
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import (
    JOB_TYPE_DAY,
    JOB_TYPE_PREDICT,
    JOB_TYPE_RACE,
    JOB_TYPE_RECOMMEND,
    JOB_TYPE_REFRESH_RANGE,
)
from .config import CONFIG

#: default freshness window — a same-race success within this many seconds is reused (US3/FR-015).
DEFAULT_FRESH_SECONDS = CONFIG.fresh_seconds

_ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
PredictOrigin = Literal["manual_ui", "auto_after_refresh"]
RefreshOrigin = Literal["manual_ui", "daily_bulk", "corner_backfill"]


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
    origin: RefreshOrigin,
    force: bool = False,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
    trace_id: str | None = None,
) -> tuple[IngestionJob, bool]:
    """Return (job, reused). Caller commits (releasing the advisory lock)."""
    if origin not in {"manual_ui", "daily_bulk", "corner_backfill"}:
        raise ValueError(f"unsupported refresh origin: {origin!r}")
    _lock_race(session, race_id)

    active = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_RACE)
        .where(IngestionJob.scope_value == race_id)
        .where(IngestionJob.status.in_(_ACTIVE))
        .order_by(IngestionJob.created_at.desc())
        .with_for_update()
    ).first()
    manual_running = (
        active is not None
        and origin == "manual_ui"
        and active.status == JobStatus.RUNNING
    )
    if active is not None:
        if origin != "manual_ui":
            return active, True
        if active.status == JobStatus.QUEUED:
            active.summary = {
                **(active.summary or {}),
                "refresh_origin": "manual_ui",
            }
            session.flush()
            return active, True
        # A user click cannot inherit a RUNNING bulk job: it may already have
        # launched predict_auto capture. Enqueue a fresh manual job instead.

    if not force and not manual_running:
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
            enqueue_predict(
                session,
                race_id,
                origin=(
                    "manual_ui"
                    if origin == "manual_ui"
                    else "auto_after_refresh"
                ),
            )
            return fresh, True

    job = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_RACE, scope="race", scope_value=race_id,
        status=JobStatus.QUEUED, trace_id=trace_id,
        summary={"refresh_origin": origin},
    )
    session.add(job)
    session.flush()
    return job, False


def enqueue_predict(
    session: Session,
    race_id: str,
    *,
    origin: PredictOrigin,
) -> tuple[IngestionJob, bool]:
    """Feature 028: enqueue a predict job (in-flight-only dedup). (job, reused); caller commits.

    Automatic callers reuse an ACTIVE job. A manual click promotes a QUEUED automatic job, but
    never rides a RUNNING job because capture may already have launched with the automatic origin.
    A completed job is not reused. The advisory lock key is `predict:{race_id}` (distinct from
    refresh's `refresh:race:{race_id}`), so predict and refresh never block each other.
    model_version is not in the dedup key (ingestion_jobs has no payload column) — it is recorded in
    prediction_runs for audit instead.
    """
    if origin not in {"manual_ui", "auto_after_refresh"}:
        raise ValueError(f"unsupported predict origin: {origin!r}")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"predict:{race_id}"})
    active = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_PREDICT)
        .where(IngestionJob.scope_value == race_id)
        .where(IngestionJob.status.in_(_ACTIVE))
        .order_by(IngestionJob.created_at.desc())
        .with_for_update()
    ).first()
    if active is not None:
        if origin != "manual_ui":
            return active, True
        if active.status == JobStatus.QUEUED:
            active.summary = {
                **(active.summary or {}),
                "predict_origin": "manual_ui",
            }
            session.flush()
            return active, True
        # A running automatic job has already read its origin. The accepted
        # cost of preserving selection provenance is a duplicate prediction.

    job = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_PREDICT, scope="race", scope_value=race_id,
        status=JobStatus.QUEUED,
        summary={
            "kind": "predict",
            "source": "manual",
            "predict_origin": origin,
        },
    )
    session.add(job)
    session.flush()
    return job, False


def enqueue_recommend(
    session: Session, race_id: str, *, source: str = "manual", reuse_running: bool = True,
    predict_origin: str | None = None,
) -> tuple[IngestionJob, bool]:
    """Feature 043: enqueue a recommend job (in-flight-only dedup). (job, reused); caller commits.

    Same shape as enqueue_predict (028): reuse only an ACTIVE (queued/running) recommend job for the
    race so a double-click can't create two; a completed job is NOT reused. The advisory lock key is
    `recommend:{race_id}` (distinct from predict/refresh), so the three never block each other. The
    generation itself is idempotent per prediction_run (betting recommend-serve skips if a set
    already exists), so re-clicking after completion is safe.

    ``source`` is an audit label: "manual" (the 買い目生成 button) or "auto_after_predict" (the
    follow-up run_predict enqueues on success so a fresh run gets its buy-ups without a second
    click). The dedup makes the two paths converge on one job when both fire.

    ``reuse_running=False`` (the auto-follow-up): a RUNNING recommend job may already have resolved
    the PRE-predict run, so reusing it as "the fresh run's buy-ups" would silently target the wrong
    run — reuse only QUEUED jobs (they resolve their run when claimed, i.e. after this predict).
    Concurrent double-generation is prevented by the betting-side per-run advisory lock.

    ``predict_origin`` (lane scheduling, codex review): the follow-up of a MANUAL predict records
    the origin so the CPU lane can rank it interactive (the user's clicked unit completes without
    a second wait) while auto_after_refresh follow-ups stay FIFO with the batch. A manual click on
    a QUEUED job promotes its source to "manual" (enqueue_predict same shape); a RUNNING job is
    never promoted (its rank was already read at claim time).
    """
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"recommend:{race_id}"})
    reusable = _ACTIVE if reuse_running else (JobStatus.QUEUED,)
    active = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_RECOMMEND)
        .where(IngestionJob.scope_value == race_id)
        .where(IngestionJob.status.in_(reusable))
        .order_by(IngestionJob.created_at.desc())
        .with_for_update()
    ).first()
    if active is not None:
        if source == "manual" and active.status == JobStatus.QUEUED:
            active.summary = {**(active.summary or {}), "source": "manual"}
            session.flush()
        return active, True

    summary: dict = {"kind": "recommend", "source": source}
    if predict_origin is not None:
        summary["predict_origin"] = predict_origin
    job = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_RECOMMEND, scope="race", scope_value=race_id,
        status=JobStatus.QUEUED, summary=summary,
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


def races_missing_corner_orders(
    session: Session, *, today: datetime.date, lookback_days: int, limit: int,
    exclude_date: datetime.date | None = None,
) -> list[str]:
    """Recent finished races whose results carry no 通過順 yet, newest first.

    netkeiba publishes the passing order about a day after the race, so a race night's ingest can
    only ever store NULL there — and `backfill_results` fills that column NULL-only, which means the
    single chance to capture it is a LATER visit. Nothing scheduled such a visit, so the column
    stayed empty for every race day that no one happened to re-run by hand.

    Gap-driven rather than age-driven on purpose: the gap is the thing we can observe, and the
    publish delay is not reliably one day (2026-08-22 was still empty 24h later). Bounded by
    ``lookback_days`` because a race old enough to still be missing is one netkeiba is not going to
    fill, and re-asking forever would spend the request budget on nothing.
    """
    if lookback_days <= 0 or limit <= 0:
        return []
    cutoff = today - datetime.timedelta(days=lookback_days)
    has_result = (
        select(RaceResult.race_id).where(RaceResult.race_id == Race.race_id).exists()
    )
    has_corners = (
        select(RaceResult.race_id)
        .where(RaceResult.race_id == Race.race_id)
        .where(RaceResult.corner_orders.is_not(None))
        .exists()
    )
    stmt = (
        select(Race.race_id)
        .where(Race.race_date >= cutoff)
        .where(Race.race_date < today)  # race night is 2% filled; there is nothing to collect yet
        .where(has_result)
        .where(~has_corners)
        .order_by(Race.race_date.desc(), Race.race_id.asc())
        .limit(limit)
    )
    if exclude_date is not None:
        stmt = stmt.where(Race.race_date != exclude_date)
    return [r for r in session.scalars(stmt) if is_valid_race_id(r)]


def enqueue_day_parent(session: Session, date: datetime.date, *,
                       force: bool = False) -> IngestionJob:
    """Create just the parent refresh_day job (QUEUED) and return it; the worker discovers the
    day's races from netkeiba and fans out refresh_race children (so the POST returns 202 without a
    netkeiba round-trip). Accepts any date — even one with no DB races yet (worker discovers).

    ``force`` is carried on the parent's summary because the flag is set at request time but is
    only consumed later, in the worker, when ``run_day`` fans the children out. Without it the
    endpoint accepted ``force`` from the published contract and silently dropped it: every child
    fell into the 600s freshness reuse, so re-pressing 「この日を更新」 inside that window issued
    no netkeiba request at all while the batch still reported done."""
    parent = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_DAY, scope="day",
        scope_value=date.isoformat(), status=JobStatus.QUEUED,
        summary={"force": bool(force)},
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
            enqueue_race(
                session,
                rid,
                origin="daily_bulk",
                force=force,
                fresh_seconds=fresh_seconds,
                trace_id=trace_id,
            )
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


def enqueue_refresh_range(
    session: Session, date_from: datetime.date, date_to: datetime.date
) -> tuple[IngestionJob, bool]:
    """Feature 053: enqueue a range refresh (live CLI: predict backfill → recommend backfill).

    In-flight-only dedup per range (advisory lock `refresh_range:{from..to}`) — a double-click
    reuses the ACTIVE job; a completed job is NOT reused (an explicit click means "run again now",
    the 050 pipeline underneath is idempotent). (job, reused); caller commits.
    """
    scope_value = f"{date_from.isoformat()}..{date_to.isoformat()}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                    {"k": f"refresh_range:{scope_value}"})
    active = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_REFRESH_RANGE)
        .where(IngestionJob.scope_value == scope_value)
        .where(IngestionJob.status.in_(_ACTIVE))
        .order_by(IngestionJob.created_at.desc())
    ).first()
    if active is not None:
        return active, True

    job = IngestionJob(
        source=Source.NETKEIBA, job_type=JOB_TYPE_REFRESH_RANGE,
        scope="range", scope_value=scope_value,
        status=JobStatus.QUEUED, summary={"kind": "refresh_range", "source": "manual"},
    )
    session.add(job)
    session.flush()
    return job, False
