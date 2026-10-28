"""chaos router (B9, 2026-10-28): GET /races/{race_id}/chaos.

The Feature 084 top-3 chaos readout (market-only, from the frozen pre-race snapshot) used to be
reachable only inside ``/races/{id}/predictions``; spec 154 moved that response to the comparison
tab, which took the readout off the main (ROI) view. This endpoint returns the identical object —
``build_race_chaos`` with the same arguments the predictions router passes — on its own path.
422 invalid_race_id / 404 race_not_found; every other state is a typed 200 (available or
unavailable with a reason).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..chaos import build_race_chaos
from ..deps import get_session
from ..queries import get_race
from ..schemas import RaceChaosResponse

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@router.get("/races/{race_id}/chaos", response_model=RaceChaosResponse, tags=["chaos"])
def race_chaos(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    race = get_race(session, race_id)
    if race is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    return RaceChaosResponse(
        race_id=race_id,
        race_chaos=build_race_chaos(session, race_id=race_id, target_date=race.race_date),
    )
