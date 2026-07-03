"""Range-refresh accept endpoint (Feature 053): enqueue a predict+recommend range backfill.

POST /refresh-range {date_from, date_to} — the worker shells out to the live CLI (050
`refresh`: predict backfill → recommend backfill, idempotent). Range is capped at 35 days so a
single job's runtime stays bounded (larger backfills belong to the CLI). Admin-SPA-facing;
returns 202 + JobAccepted like the other job endpoints.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import API_PREFIX
from ..deps import get_session
from ..enqueue import enqueue_refresh_range
from ..schemas import ErrorBody, JobAccepted

router = APIRouter(tags=["refresh-range"])

_MAX_DAYS = 35
_ERRORS = {422: {"model": ErrorBody}}


class RefreshRangeRequest(BaseModel):
    date_from: datetime.date
    date_to: datetime.date


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": status, "code": code, "detail": detail})


@router.post("/refresh-range", status_code=202, response_model=JobAccepted, responses=_ERRORS)
def refresh_range(body: RefreshRangeRequest, session: Session = Depends(get_session)):
    if body.date_from > body.date_to:
        return _err(422, "invalid_range", "date_from must be <= date_to")
    if (body.date_to - body.date_from).days + 1 > _MAX_DAYS:
        return _err(422, "range_too_wide",
                    f"range must be <= {_MAX_DAYS} days (larger backfills: use the live CLI)")
    job, reused = enqueue_refresh_range(session, body.date_from, body.date_to)
    session.commit()
    return JobAccepted(
        job_id=job.ingestion_job_id, status=job.status, reused=reused, scope="range",
        scope_value=job.scope_value, poll_url=f"{API_PREFIX}/jobs/{job.ingestion_job_id}",
    )
