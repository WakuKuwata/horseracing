"""Job/batch status endpoints (Feature 024) — read-only polling targets for the front.

GET /jobs/{job_id}        — one refresh job's status (US1)
GET /batches/{trace_id}   — a day batch's aggregate + children (US2)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import JOB_TYPE_DAY, JOB_TYPE_RACE
from ..deps import get_session
from ..enqueue import batch_status
from ..schemas import Batch, ErrorBody, Job

router = APIRouter(tags=["jobs"])
_ERRORS = {404: {"model": ErrorBody}}


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": status, "code": code, "detail": detail})


def _to_job(j: IngestionJob) -> Job:
    summary = j.summary if isinstance(j.summary, dict) else {}
    return Job(
        job_id=j.ingestion_job_id, job_type=j.job_type, status=j.status, scope=j.scope,
        scope_value=j.scope_value, trace_id=j.trace_id, kind=summary.get("kind"),
        reason=summary.get("reason"), followup_job_id=summary.get("recommend_job_id"),
        processed_rows=j.processed_rows, skipped_rows=j.skipped_rows, error_count=j.error_count,
        retry_count=j.retry_count, started_at=j.started_at, completed_at=j.completed_at,
        error_message=j.error_message,
    )


@router.get("/jobs/{job_id}", response_model=Job, responses=_ERRORS)
def get_job(job_id: uuid.UUID, session: Session = Depends(get_session)):
    j = session.get(IngestionJob, job_id)
    if j is None:
        return _err(404, "job_not_found", f"job {job_id} not found")
    return _to_job(j)


def _latest_jobs_by_race(session: Session, race_ids: list[str]) -> dict[str, IngestionJob]:
    """The CURRENT refresh_race job for each race, whichever batch enqueued it.

    DISTINCT ON keeps one row per race — the newest, with the id as the deterministic tie-break
    for jobs that share a created_at (a fan-out writes all of its children in one transaction).
    """
    if not race_ids:
        return {}
    rows = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_RACE)
        .where(IngestionJob.scope_value.in_(race_ids))
        .distinct(IngestionJob.scope_value)
        .order_by(
            IngestionJob.scope_value.asc(),
            IngestionJob.created_at.desc(),
            IngestionJob.ingestion_job_id.desc(),
        )
    ).all()
    return {j.scope_value: j for j in rows if j.scope_value}


@router.get("/batches/{trace_id}", response_model=Batch, responses=_ERRORS)
def get_batch(trace_id: str, session: Session = Depends(get_session)):
    """Report the state of THE DAY this batch refreshed — not merely of the rows it created.

    A child that `enqueue_race` reused (already active, or refreshed inside the freshness window)
    keeps its ORIGINAL trace_id, so a trace-scoped query cannot see it. That made the aggregate
    describe an arbitrary subset: a day where 20 of 36 races were reused reported
    「完了 16/16 成功」 and, when every race was reused, 「完了 0/0 成功」 — and a reused race that
    later FAILED was reported nowhere at all.

    So membership is resolved from the race ids the parent discovered, and each race contributes
    its CURRENT job whoever enqueued it. This deliberately does not try to make one job belong to
    two batches; the batch is a question about a day, and it is answered about that day.
    """
    # the parent refresh_day exists from the moment the POST is accepted — BEFORE the worker has
    # discovered/fanned-out children. Recognise it so polling never 404s during that window.
    parent = None
    try:
        cand = session.get(IngestionJob, uuid.UUID(trace_id))
        if cand is not None and cand.job_type == JOB_TYPE_DAY:
            parent = cand
    except ValueError:
        parent = None

    parent_summary = parent.summary if (parent is not None and isinstance(parent.summary, dict)) \
        else {}
    race_ids = parent_summary.get("race_ids")
    race_ids = [r for r in race_ids if isinstance(r, str)] if isinstance(race_ids, list) else None

    if race_ids is not None:
        latest = _latest_jobs_by_race(session, race_ids)
        children = [latest[r] for r in sorted(race_ids) if r in latest]
        # A discovered race with no job at all is not yet accounted for. Count it as still running
        # rather than dropping it, so the batch never reports done while a race is unrepresented.
        statuses = [latest[r].status if r in latest else JobStatus.RUNNING for r in race_ids]
        total = len(race_ids)
    else:
        # Parents from before race_ids was recorded (and any non-day trace): trace-scoped,
        # exactly as before.
        children = list(session.scalars(
            select(IngestionJob)
            .where(IngestionJob.trace_id == trace_id)
            .where(IngestionJob.job_type == JOB_TYPE_RACE)
            .order_by(IngestionJob.scope_value.asc())
        ).all())
        statuses = [c.status for c in children]
        total = len(children)

    if not children and parent is None:
        return _err(404, "batch_not_found", f"batch {trace_id} not found")

    discovered = parent_summary.get("races")
    if not isinstance(discovered, int):
        discovered = None
    enqueued = parent_summary.get("children_new")
    if not isinstance(enqueued, int):
        enqueued = None

    # with members, aggregate them; before discovery, reflect the parent's own status (queued/
    # running) so a not-yet-discovered batch isn't reported done prematurely.
    if statuses:
        status = batch_status(statuses)
    else:
        status = parent.status if parent is not None else JobStatus.SUCCEEDED
    return Batch(
        trace_id=trace_id, status=status,
        scope_value=parent.scope_value if parent is not None else None,
        total=total,
        succeeded=sum(1 for s in statuses if s == JobStatus.SUCCEEDED),
        failed=sum(1 for s in statuses if s == JobStatus.FAILED),
        running=sum(1 for s in statuses if s in (JobStatus.QUEUED, JobStatus.RUNNING)),
        discovered=discovered,
        enqueued=enqueued,
        partial=sum(1 for s in statuses if s == JobStatus.PARTIAL),
        skipped=sum(1 for s in statuses if s == JobStatus.SKIPPED),
        children=[_to_job(c) for c in children],
    )
