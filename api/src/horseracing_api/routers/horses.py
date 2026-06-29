"""Horse profile endpoints (Feature 029) — read-only.

GET /horses/{id}          — identity + pedigree (names) + career aggregates (facts, not features)
GET /horses/{id}/history  — race-by-race entries, newest first, paged

Career stats are factual aggregates over race_horses + race_results (NOT model features); they never
re-enter the model (constitution II). horse_id has no fixed format, so a missing horse is 404 (no
422). Read-only: every route is a GET over a read-only session.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import horse_history, horse_profile
from ..schemas import HorseHistoryRow, HorseProfile, Page

router = APIRouter(tags=["horses"])

_MAX_PAGE_SIZE = 200


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _rate(num: int, denom: int) -> float | None:
    return (num / denom) if denom else None


def _secs(td: datetime.timedelta | None) -> float | None:
    return td.total_seconds() if td is not None else None


@router.get("/horses/{horse_id}", response_model=HorseProfile,
            responses={404: {"description": "horse not found"}})
def get_horse(horse_id: str, session: Session = Depends(get_session)):
    data = horse_profile(session, horse_id)
    if data is None:
        return _err(404, "horse_not_found", f"horse {horse_id} not found")
    h = data.horse
    return HorseProfile(
        horse_id=h.horse_id, horse_name=h.horse_name, sex=h.sex, birth_year=h.birth_year,
        data_source=h.data_source, sire_name=h.sire_name, dam_name=h.dam_name,
        damsire_name=h.damsire_name,
        starts=data.starts, wins=data.wins, seconds_in=data.seconds_in, shows_in=data.shows_in,
        win_rate=_rate(data.wins, data.starts),
        quinella_rate=_rate(data.seconds_in, data.starts),
        show_rate=_rate(data.shows_in, data.starts),
        avg_finish=data.avg_finish,
    )


@router.get("/horses/{horse_id}/history", response_model=Page[HorseHistoryRow],
            responses={404: {"description": "horse not found"}})
def get_horse_history(
    horse_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    session: Session = Depends(get_session),
):
    result = horse_history(session, horse_id, page=page, page_size=page_size)
    if result is None:
        return _err(404, "horse_not_found", f"horse {horse_id} not found")
    rows, total = result
    items = [
        HorseHistoryRow(
            race_id=r.race_id, race_date=r.race_date, venue_code=r.venue_code,
            race_number=r.race_number, race_name=r.race_name, race_class=r.race_class,
            distance=r.distance, track_type=r.track_type, horse_number=r.horse_number,
            popularity=r.popularity, odds=(float(r.odds) if r.odds is not None else None),
            entry_status=r.entry_status, finish_order=r.finish_order,
            finish_time_sec=_secs(r.finish_time),
            last_3f=(float(r.last_3f) if r.last_3f is not None else None),
            result_status=r.result_status,
        )
        for r in rows
    ]
    return Page[HorseHistoryRow](
        items=items, page=page, page_size=page_size, total=total,
        has_next=(page * page_size) < total,
    )
