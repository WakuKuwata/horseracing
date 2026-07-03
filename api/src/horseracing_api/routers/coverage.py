"""coverage router (Feature 052 admin console): GET /coverage — per-day product coverage.

Read-only grouped aggregation (races / odds / results / active-model predictions /
recommendations per race day). Range is REQUIRED and capped at 400 days (typed 422) so a single
request can never scan the whole 2007+ table unbounded. Consumed by the admin SPA.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import active_model_version, coverage_by_date
from ..schemas import CoverageDay, CoverageResponse

router = APIRouter()

_MAX_DAYS = 400


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@router.get("/coverage", response_model=CoverageResponse, tags=["coverage"])
def coverage(
    date_from: datetime.date,
    date_to: datetime.date,
    session: Session = Depends(get_session),
):
    if date_from > date_to:
        return _err(422, "invalid_range", "date_from must be <= date_to")
    if (date_to - date_from).days + 1 > _MAX_DAYS:
        return _err(422, "range_too_wide", f"range must be <= {_MAX_DAYS} days")
    days = coverage_by_date(session, date_from, date_to)
    return CoverageResponse(
        date_from=date_from,
        date_to=date_to,
        active_model_version=active_model_version(session),
        days=[CoverageDay(**d) for d in days],
    )
