"""market-ev router (Feature 137): /races/{race_id}/market-ev (+ B7: /market-ev/day).

Read-only view of the SEPARATE market-aware model's stored expected return (期待回収率). There is
no ``model_version`` query parameter: this is independent of the win-probability model selection
on /predictions. 422 invalid_race_id / 404 race_not_found; every other state is a typed 200
(``available`` or ``unavailable`` with a reason). Shaping and the threshold live in market_ev.py.

Feature 138 (D8): the version shown is the registry constant ``DISPLAYED_MARKET_EV_MODEL_VERSION``
(the 15-seed average); a newer single-seed recompute of the same race never replaces the column.

B7 (2026-10-28): ``GET /market-ev/day?date=`` — the day's races that have stored rows, each folded
from the same shaping (state, provenance, counts above the thresholds). ``date`` is required
(missing or malformed → 422 validation_error); a day without races or rows is 200 with
``items=[]``. Board order only.

B8 (2026-10-28): ``GET /market-ev/prospective`` — the two columns' prospective record (genuine
pre-race quotes settled at the official win payout, spec 158's rules), counts and interval only.
"""

from __future__ import annotations

import datetime
import re
from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..market_ev import (
    DISPLAYED_MARKET_EV_MODEL_VERSION,
    build_market_ev,
    build_market_ev_day,
)
from ..market_ev_prospective import (
    build_market_ev_prospective,
    earliest_start_date,
    races_from_queries,
)
from ..queries import (
    entries_for_races,
    get_race,
    market_ev_rows,
    market_ev_rows_for_races,
    market_ev_rows_since,
    official_win_payouts_for_races,
    race_ids_with_results,
    race_results_for_races,
    races_on_date,
    started_win_odds_by_horse,
    started_win_odds_by_race,
)
from ..schemas import MarketEvDayResponse, MarketEvProspectiveResponse, MarketEvResponse

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
    race = get_race(session, race_id)
    if race is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    return build_market_ev(
        race_id,
        market_ev_rows(session, race_id, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION),
        started_win_odds_by_horse(session, race_id),
        race_date=race.race_date,
    )


@router.get("/market-ev/day", response_model=MarketEvDayResponse, tags=["market-ev"])
def market_ev_day(date: datetime.date, session: Session = Depends(get_session)):
    races = races_on_date(session, date)
    race_ids = [r.race_id for r in races]
    rows_by_race: dict[str, list] = defaultdict(list)
    for row in market_ev_rows_for_races(
        session, race_ids, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION
    ):
        rows_by_race[row.race_id].append(row)
    computed_ids = [rid for rid in race_ids if rid in rows_by_race]
    return build_market_ev_day(
        date=date,
        races=races,
        rows_by_race=rows_by_race,
        started_odds_by_race=started_win_odds_by_race(session, computed_ids),
        race_ids_with_results=race_ids_with_results(session, computed_ids),
    )


@router.get(
    "/market-ev/prospective", response_model=MarketEvProspectiveResponse, tags=["market-ev"]
)
def market_ev_prospective(session: Session = Depends(get_session)):
    rows_with_race = market_ev_rows_since(
        session, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION, since=earliest_start_date()
    )
    race_ids = sorted({row.race_id for row, _race in rows_with_race})
    races = races_from_queries(
        rows_with_race,
        entries_for_races(session, race_ids),
        race_results_for_races(session, race_ids),
        official_win_payouts_for_races(session, race_ids),
    )
    return build_market_ev_prospective(races, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION)
