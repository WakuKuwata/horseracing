"""odds router (Feature 014): /races/{race_id}/odds.

Real vs estimated odds are kept in SEPARATE response fields and labeled (odds_source/is_estimated):
- win: real single-win odds from race_horses (updated_at).
- estimated: 010 estimate_market_odds RE-COMPUTED now on the canonical field (pseudo, as_of). win
  estimated always (small); an exotic bet_type is added top-K only when requested (grid protection).
- real_exotic: 012 real dividend odds from exotic_odds (coverage_scope, updated_at).
Missing odds yield a 200 typed-empty section (MarketOddsError is caught here, never a 500).
"""

from __future__ import annotations

import datetime
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from horseracing_db.enums import BetType
from horseracing_db.selection import canonical_selection
from horseracing_probability.market_odds import MarketOddsError, estimate_market_odds
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import canonical_win_odds, get_race, real_exotic_odds, win_odds
from ..schemas import EstimatedOddsRow, OddsResponse, RealExoticOddsRow, WinOddsRow

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")
_EXOTIC = set(BetType.EXOTIC)


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _numbers(key) -> list[int]:
    return [int(key)] if isinstance(key, int) else [int(x) for x in key]


@router.get("/races/{race_id}/odds", response_model=OddsResponse, tags=["odds"])
def odds(
    race_id: str,
    bet_type: str | None = Query(default=None),
    top: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if get_race(session, race_id) is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    if bet_type is not None and bet_type not in _EXOTIC:
        return _err(422, "invalid_bet_type", f"bet_type must be one of {sorted(_EXOTIC)}")

    win = [
        WinOddsRow(horse_number=n, horse_id=hid,
                   odds=(float(o) if o is not None else None), updated_at=u)
        for (n, hid, o, u) in win_odds(session, race_id)
    ]

    as_of = datetime.datetime.now(datetime.UTC)
    estimated: list[EstimatedOddsRow] = []
    canon = canonical_win_odds(session, race_id)
    if len(canon) >= 2:
        try:
            eo = estimate_market_odds(canon, field_size=len(canon))
            # win estimated (per-horse, small) — selection is a single horse number
            for n, o in (eo.win or {}).items():
                estimated.append(EstimatedOddsRow(bet_type="win", selection=[int(n)],
                                                  odds=(float(o) if o is not None else None),
                                                  as_of=as_of))
            if bet_type is not None:
                emap = {BetType.PLACE: eo.place, BetType.QUINELLA: eo.quinella,
                        BetType.EXACTA: eo.exacta, BetType.WIDE: eo.wide,
                        BetType.TRIO: eo.trio, BetType.TRIFECTA: eo.trifecta}.get(bet_type)
                rows = [
                    EstimatedOddsRow(bet_type=bet_type,
                                     selection=canonical_selection(bet_type, _numbers(k)),
                                     odds=float(v), as_of=as_of)
                    for k, v in (emap or {}).items() if v is not None
                ]
                rows.sort(key=lambda r: (r.odds, r.selection))  # lowest odds = most likely
                estimated.extend(rows[:top])
        except MarketOddsError:
            estimated = []  # typed-empty, never 500

    real_exotic = [
        RealExoticOddsRow(bet_type=x.bet_type, selection=[int(i) for i in x.selection],
                          odds=float(x.odds), coverage_scope=x.coverage_scope,
                          updated_at=x.updated_at)
        for x in real_exotic_odds(session, race_id)
    ]
    return OddsResponse(race_id=race_id, win=win, estimated=estimated, real_exotic=real_exotic)
