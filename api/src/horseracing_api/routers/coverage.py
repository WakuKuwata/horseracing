"""coverage router (Feature 052 admin console): GET /coverage — per-day product coverage.

Read-only grouped aggregation (races / odds / results / active-model predictions /
recommendations per race day). Range is REQUIRED and capped at 400 days (typed 422) so a single
request can never scan the whole 2007+ table unbounded. Consumed by the admin SPA.

B11 (2026-10-28): three ROI columns per day — races with stored 期待回収率 rows of the displayed
version, of those the races whose rows all carry 140's genuine-quote token, and of those the races
whose 141 mixture is computable from the stored rows (market_ev.market_ev_coverage_by_date).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..market_ev import (
    DISPLAYED_MARKET_EV_MODEL_VERSION,
    QUOTE_TOKEN,
    market_ev_coverage_by_date,
)
from ..queries import active_model_version, coverage_by_date, market_ev_coverage_rows
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
    roi = market_ev_coverage_by_date(market_ev_coverage_rows(
        session, date_from, date_to, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION
    ))
    empty = {"computed": 0, "quote": 0, "mix_available": 0}
    return CoverageResponse(
        date_from=date_from,
        date_to=date_to,
        active_model_version=active_model_version(session),
        market_ev_model_version=DISPLAYED_MARKET_EV_MODEL_VERSION,
        quote_token=QUOTE_TOKEN,
        days=[
            CoverageDay(
                **d,
                n_market_ev_computed=roi.get(d["date"], empty)["computed"],
                n_market_ev_quote=roi.get(d["date"], empty)["quote"],
                n_market_ev_mix_available=roi.get(d["date"], empty)["mix_available"],
            )
            for d in days
        ],
    )
