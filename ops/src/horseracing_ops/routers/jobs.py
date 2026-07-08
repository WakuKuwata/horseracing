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


@router.get("/batches/{trace_id}", response_model=Batch, responses=_ERRORS)
def get_batch(trace_id: str, session: Session = Depends(get_session)):
    children = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.trace_id == trace_id)
        .where(IngestionJob.job_type == JOB_TYPE_RACE)
        .order_by(IngestionJob.scope_value.asc())
    ).all()
    # the parent refresh_day exists from the moment the POST is accepted — BEFORE the worker has
    # discovered/fanned-out children. Recognise it so polling never 404s during that window.
    parent = None
    try:
        cand = session.get(IngestionJob, uuid.UUID(trace_id))
        if cand is not None and cand.job_type == JOB_TYPE_DAY:
            parent = cand
    except ValueError:
        parent = None
    if not children and parent is None:
        return _err(404, "batch_not_found", f"batch {trace_id} not found")

    statuses = [c.status for c in children]
    # with children, aggregate them; before discovery, reflect the parent's own status (queued/
    # running) so a not-yet-discovered batch isn't reported done prematurely.
    if children:
        status = batch_status(statuses)
    else:  # before discovery: reflect the parent's own status (don't report done prematurely)
        status = parent.status if parent is not None else JobStatus.SUCCEEDED
    return Batch(
        trace_id=trace_id, status=status,
        scope_value=parent.scope_value if parent is not None else None,
        total=len(children),
        succeeded=sum(1 for s in statuses if s == JobStatus.SUCCEEDED),
        failed=sum(1 for s in statuses if s == JobStatus.FAILED),
        running=sum(1 for s in statuses if s in (JobStatus.QUEUED, JobStatus.RUNNING)),
        children=[_to_job(c) for c in children],
    )
