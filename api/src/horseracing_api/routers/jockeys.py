"""Jockey profile endpoints (Feature 029, US2) — read-only.

GET /jockeys/{id}          — identity + riding aggregates (facts, not features)
GET /jockeys/{id}/history  — recent mounts, newest first, paged

Like horses, jockey_id has no fixed format, so a missing jockey is 404 (no 422). Aggregates are
factual (race_horses + race_results) and never re-enter the model (constitution II).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import jockey_history, jockey_profile
from ..schemas import JockeyHistoryRow, JockeyProfile, Page

router = APIRouter(tags=["jockeys"])

_MAX_PAGE_SIZE = 200


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _rate(num: int, denom: int) -> float | None:
    return (num / denom) if denom else None


@router.get("/jockeys/{jockey_id}", response_model=JockeyProfile,
            responses={404: {"description": "jockey not found"}})
def get_jockey(jockey_id: str, session: Session = Depends(get_session)):
    data = jockey_profile(session, jockey_id)
    if data is None:
        return _err(404, "jockey_not_found", f"jockey {jockey_id} not found")
    j = data.jockey
    return JockeyProfile(
        jockey_id=j.jockey_id, jockey_name=j.jockey_name,
        mounts=data.mounts, wins=data.wins, seconds_in=data.seconds_in, shows_in=data.shows_in,
        win_rate=_rate(data.wins, data.mounts),
        quinella_rate=_rate(data.seconds_in, data.mounts),
        show_rate=_rate(data.shows_in, data.mounts),
        avg_finish=data.avg_finish,
    )


@router.get("/jockeys/{jockey_id}/history", response_model=Page[JockeyHistoryRow],
            responses={404: {"description": "jockey not found"}})
def get_jockey_history(
    jockey_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    session: Session = Depends(get_session),
):
    result = jockey_history(session, jockey_id, page=page, page_size=page_size)
    if result is None:
        return _err(404, "jockey_not_found", f"jockey {jockey_id} not found")
    rows, total = result
    items = [
        JockeyHistoryRow(
            race_id=r.race_id, race_date=r.race_date, venue_code=r.venue_code,
            race_number=r.race_number, race_name=r.race_name, horse_id=r.horse_id,
            horse_name=r.horse_name, finish_order=r.finish_order, result_status=r.result_status,
        )
        for r in rows
    ]
    return Page[JockeyHistoryRow](
        items=items, page=page, page_size=page_size, total=total,
        has_next=(page * page_size) < total,
    )
