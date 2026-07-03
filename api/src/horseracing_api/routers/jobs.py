"""jobs router (Feature 052 admin console): GET /jobs — ingestion_jobs history.

Read-only newest-first job audit list (the ops service only exposes single-job polling; this
closes the list/filter blind spot). Exact-match filters — an unknown status/job_type returns an
empty list, never an error. limit defaults to 50, capped at 200.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import list_jobs
from ..schemas import JobListResponse, JobRow

router = APIRouter()


@router.get("/jobs", response_model=JobListResponse, tags=["jobs"])
def jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    rows = list_jobs(session, status=status, job_type=job_type, limit=limit)
    return JobListResponse(items=[
        JobRow(
            ingestion_job_id=str(j.ingestion_job_id),
            source=j.source, job_type=j.job_type,
            scope=j.scope, scope_value=j.scope_value,
            status=j.status, trace_id=j.trace_id, retry_count=j.retry_count,
            started_at=j.started_at, completed_at=j.completed_at,
            error_message=j.error_message,
            processed_rows=j.processed_rows, skipped_rows=j.skipped_rows,
            error_count=j.error_count, created_at=j.created_at,
        )
        for j in rows
    ])
