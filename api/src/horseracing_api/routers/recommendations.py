"""recommendations router (Feature 014): /races/{race_id}/recommendations.

READ-ONLY: returns persisted ``recommendations`` rows (exotic bet types only — win recs store a dict
selection, out of this list[int] contract). NEVER calls generate_exotic_recommendations (a write);
this module does not import horseracing_betting at all. double_pseudo = is_estimated_odds so the
front cannot present a pseudo-ROI as a realized one.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import exotic_recommendations, get_race
from ..schemas import RecommendationResponse, RecommendationRow

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")


def _f(x):
    return float(x) if x is not None else None


@router.get("/races/{race_id}/recommendations", response_model=RecommendationResponse,
            tags=["recommendations"])
def recommendations(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return JSONResponse(status_code=422, content={
            "status": 422, "code": "invalid_race_id", "detail": "race_id must be 12 digits"})
    if get_race(session, race_id) is None:
        return JSONResponse(status_code=404, content={
            "status": 404, "code": "race_not_found", "detail": f"race {race_id} not found"})
    items = [
        RecommendationRow(
            bet_type=r.bet_type, selection=[int(i) for i in r.selection],
            market_odds_used=_f(r.market_odds_used),
            estimated_market_odds_used=_f(r.estimated_market_odds_used),
            is_estimated_odds=r.is_estimated_odds, pseudo_odds=_f(r.pseudo_odds),
            pseudo_roi=_f(r.pseudo_roi), double_pseudo=bool(r.is_estimated_odds),
            logic_version=r.logic_version, computed_at=r.computed_at,
            prediction_run_id=str(r.prediction_run_id),
        )
        for r in exotic_recommendations(session, race_id)
    ]
    return RecommendationResponse(race_id=race_id, items=items)
