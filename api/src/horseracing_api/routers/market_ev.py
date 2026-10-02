"""market-ev router (Feature 137): /races/{race_id}/market-ev.

Read-only view of the SEPARATE market-aware model's stored expected return (期待回収率). There is
no ``model_version`` query parameter: this is independent of the win-probability model selection
on /predictions. 422 invalid_race_id / 404 race_not_found; every other state is a typed 200
(``available`` or ``unavailable`` with a reason). Shaping and the threshold live in market_ev.py.

Feature 138 (D8): the version shown is the registry constant ``DISPLAYED_MARKET_EV_MODEL_VERSION``
(the 15-seed average); a newer single-seed recompute of the same race never replaces the column.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..market_ev import DISPLAYED_MARKET_EV_MODEL_VERSION, build_market_ev
from ..queries import get_race, market_ev_rows, started_win_odds_by_horse
from ..schemas import MarketEvResponse

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@router.get("/races/{race_id}/market-ev", response_model=MarketEvResponse, tags=["market-ev"])
def race_market_ev(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if get_race(session, race_id) is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    return build_market_ev(
        race_id,
        market_ev_rows(session, race_id, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION),
        started_win_odds_by_horse(session, race_id),
    )
