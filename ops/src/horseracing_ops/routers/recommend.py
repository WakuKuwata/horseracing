"""Recommend-accept endpoint (Feature 043): enqueue a buy-recommendation job and return 202.

POST /races/{race_id}/recommend — generate the product recommendation set (Kelly EV+stake) for one
race by shelling out to the betting CLI (write path, ops owner role). Distinct from 028 predict:
recommendations need a prediction_run + odds. The worker runs betting recommend-serve (idempotent);
the front polls /jobs/{job_id} and, on success, refetches the 014 recommendations.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import API_PREFIX
from ..deps import get_session
from ..enqueue import enqueue_recommend, race_exists
from ..schemas import ErrorBody, JobAccepted

router = APIRouter(tags=["recommend"])

_RACE_ID = re.compile(r"^[0-9]{12}$")
_ERRORS = {404: {"model": ErrorBody}, 422: {"model": ErrorBody}}


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": status, "code": code, "detail": detail})


@router.post("/races/{race_id}/recommend", status_code=202, response_model=JobAccepted,
             responses=_ERRORS)
def recommend_race(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if not race_exists(session, race_id):
        return _err(404, "race_not_found", f"race {race_id} not found")
    job, reused = enqueue_recommend(session, race_id)
    return JobAccepted(
        job_id=job.ingestion_job_id, status=job.status, reused=reused,
        scope_value=job.scope_value, poll_url=f"{API_PREFIX}/jobs/{job.ingestion_job_id}",
    )
