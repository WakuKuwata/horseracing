"""races router (Feature 014): /health, /races (filtered, paginated), /races/{race_id}."""

from __future__ import annotations

import datetime
import functools
import pathlib
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import API_VERSION, SCHEMA_VERSION
from ..deps import get_session
from ..queries import (
    get_race,
    list_races,
    race_has_results,
    race_horses,
    race_ids_with_results,
)
from ..schemas import HorseEntry, Page, RaceDetail, RaceSummary

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")
_MAX_PAGE_SIZE = 200


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _summary(r, *, has_results: bool = False) -> RaceSummary:
    return RaceSummary(
        race_id=r.race_id, race_date=r.race_date, venue_code=r.venue_code,
        race_number=r.race_number, race_name=r.race_name, race_class=r.race_class,
        distance=r.distance, track_type=r.track_type, post_time=r.post_time,
        has_results=has_results,
    )


@functools.lru_cache(maxsize=1)
def _alembic_head() -> str | None:
    """Migration head bundled in the image (read from db/migrations). Cached (cheap, file scan).

    Resolves db/ relative to the editable-installed horseracing_db package
    (.../db/src/horseracing_db → parents[2] = db/). Feature 018: lets /health detect a DB whose
    schema is not at head — read-only, no schema dependency added.
    """
    import os

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    loc = os.getenv("ALEMBIC_SCRIPT_LOCATION")  # set in the deploy image; robust to install mode
    if not loc:
        import horseracing_db

        loc = str(pathlib.Path(horseracing_db.__file__).resolve().parents[2] / "migrations")
    cfg = Config()
    cfg.set_main_option("script_location", loc)
    return ScriptDirectory.from_config(cfg).get_current_head()


@router.get("/health", tags=["meta"], response_model=None)
def health(session: Session = Depends(get_session)) -> JSONResponse | dict:
    """Read-only readiness: DB connectivity + alembic schema-at-head (Feature 018, fail-closed).

    503 when the DB is unreachable OR applied migration != bundled head, so the deploy healthcheck
    blocks serving on an un-migrated DB. SELECT-only — no write path added (014 stays read-only).
    """
    head = _alembic_head()
    try:
        session.execute(text("SELECT 1"))
        current = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        db_ok = True
    except Exception:
        current, db_ok = None, False
    in_sync = bool(db_ok and current == head)
    body = {
        "status": "ok" if in_sync else "unhealthy",
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "db": db_ok,
        "alembic_current": current,
        "alembic_head": head,
        "schema_in_sync": in_sync,
    }
    if not in_sync:
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/races", response_model=Page[RaceSummary], tags=["races"])
def races(
    date: datetime.date | None = Query(default=None),
    venue: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    session: Session = Depends(get_session),
) -> Page[RaceSummary]:
    rows, total = list_races(session, date=date, venue=venue, page=page, page_size=page_size)
    with_results = race_ids_with_results(session, [r.race_id for r in rows])
    return Page[RaceSummary](
        items=[_summary(r, has_results=r.race_id in with_results) for r in rows],
        page=page, page_size=page_size,
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
        HorseEntry(
            horse_number=h.horse_number, frame=h.frame, horse_id=h.horse_id,
            horse_name=h.horse_name, entry_status=h.entry_status, age=h.age, sex=h.sex,
            jockey_name=h.jockey_name, trainer_name=h.trainer_name,
            jockey_weight=float(h.jockey_weight) if h.jockey_weight is not None else None,
            weight=h.weight, weight_diff=h.weight_diff,
            odds=float(h.odds) if h.odds is not None else None, popularity=h.popularity,
        )
        for h in race_horses(session, race_id)
    ]
    summary = _summary(r, has_results=race_has_results(session, race_id))
    return RaceDetail(**summary.model_dump(), horses=horses)
