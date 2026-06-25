"""races router (Feature 014): /health, /races (filtered, paginated), /races/{race_id}."""

from __future__ import annotations

import datetime
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import API_VERSION, SCHEMA_VERSION
from ..deps import get_session
from ..queries import get_race, list_races, race_horses
from ..schemas import HorseEntry, Page, RaceDetail, RaceSummary

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")
_MAX_PAGE_SIZE = 200


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _summary(r) -> RaceSummary:
    return RaceSummary(
        race_id=r.race_id, race_date=r.race_date, venue_code=r.venue_code,
        race_number=r.race_number, race_class=r.race_class, distance=r.distance,
        track_type=r.track_type,
    )


@router.get("/health", tags=["meta"])
def health(session: Session = Depends(get_session)) -> dict:
    session.execute(text("SELECT 1"))  # DB readiness
    return {"status": "ok", "api_version": API_VERSION, "schema_version": SCHEMA_VERSION}


@router.get("/races", response_model=Page[RaceSummary], tags=["races"])
def races(
    date: datetime.date | None = Query(default=None),
    venue: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    session: Session = Depends(get_session),
) -> Page[RaceSummary]:
    rows, total = list_races(session, date=date, venue=venue, page=page, page_size=page_size)
    return Page[RaceSummary](
        items=[_summary(r) for r in rows], page=page, page_size=page_size,
        total=total, has_next=(page * page_size) < total,
    )


@router.get("/races/{race_id}", response_model=RaceDetail, tags=["races"],
            responses={404: {"description": "race not found"}, 422: {"description": "bad race_id"}})
def race_detail(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    r = get_race(session, race_id)
    if r is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    horses = [
        HorseEntry(horse_number=h.horse_number, horse_id=h.horse_id, entry_status=h.entry_status,
                   age=h.age, sex=h.sex)
        for h in race_horses(session, race_id)
    ]
    return RaceDetail(**_summary(r).model_dump(), horses=horses)
