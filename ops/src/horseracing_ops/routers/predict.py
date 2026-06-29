"""Predict-accept endpoint (Feature 028): enqueue a model-prediction job and return 202.

POST /races/{race_id}/predict — generate the active model's predictions for one race (US1).

Write path (ops, owner role) — the read-only 014 API never generates. The worker runs
serving.run_serving; the front polls /jobs/{job_id} and, on success, refetches 014 predictions.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import API_PREFIX
from ..deps import get_session
from ..enqueue import enqueue_predict, race_exists
from ..schemas import ErrorBody, JobAccepted

router = APIRouter(tags=["predict"])

_RACE_ID = re.compile(r"^[0-9]{12}$")
_ERRORS = {404: {"model": ErrorBody}, 422: {"model": ErrorBody}}


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": status, "code": code, "detail": detail})


@router.post("/races/{race_id}/predict", status_code=202, response_model=JobAccepted,
             responses=_ERRORS)
def predict_race(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if not race_exists(session, race_id):
        return _err(404, "race_not_found", f"race {race_id} not found")
    job, reused = enqueue_predict(session, race_id)
    return JobAccepted(
        job_id=job.ingestion_job_id, status=job.status, reused=reused,
        scope_value=job.scope_value, poll_url=f"{API_PREFIX}/jobs/{job.ingestion_job_id}",
    )
