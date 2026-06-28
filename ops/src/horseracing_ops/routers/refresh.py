"""Refresh-accept endpoints (Feature 024): enqueue and return 202 immediately (never block).

POST /races/{race_id}/refresh  — 1 race (US1)
POST /days/{date}/refresh      — all races on a date (US2)
"""

from __future__ import annotations

import datetime
import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import API_PREFIX
from ..deps import get_session
from ..enqueue import batch_status, enqueue_day, enqueue_race, race_exists
from ..schemas import BatchAccepted, ErrorBody, JobAccepted, RefreshRequest

router = APIRouter(tags=["refresh"])

_RACE_ID = re.compile(r"^[0-9]{12}$")
_ERRORS = {404: {"model": ErrorBody}, 422: {"model": ErrorBody}}


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": status, "code": code, "detail": detail})


def _job_poll(job_id) -> str:
    return f"{API_PREFIX}/jobs/{job_id}"


def _accepted(job, reused: bool) -> JobAccepted:
    return JobAccepted(job_id=job.ingestion_job_id, status=job.status, reused=reused,
                       scope_value=job.scope_value, poll_url=_job_poll(job.ingestion_job_id))


@router.post("/races/{race_id}/refresh", status_code=202, response_model=JobAccepted,
             responses=_ERRORS)
def refresh_race(race_id: str, body: RefreshRequest | None = None,
                 session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if not race_exists(session, race_id):
        return _err(404, "race_not_found", f"race {race_id} not found")
    force = bool(body.force) if body else False
    job, reused = enqueue_race(session, race_id, force=force)
    return _accepted(job, reused)


@router.post("/days/{date}/refresh", status_code=202, response_model=BatchAccepted,
             responses=_ERRORS)
def refresh_day(date: datetime.date, body: RefreshRequest | None = None,
                session: Session = Depends(get_session)):
    force = bool(body.force) if body else False
    parent, children = enqueue_day(session, date, force=force)
    if not children:
        return _err(404, "no_races_on_date", f"no races on {date.isoformat()}")
    child_models = [_accepted(j, reused) for j, reused in children]
    statuses = [j.status for j, _ in children]
    return BatchAccepted(
        trace_id=parent.trace_id, status=batch_status(statuses),
        scope_value=parent.scope_value, poll_url=f"{API_PREFIX}/batches/{parent.trace_id}",
        children=child_models,
    )
