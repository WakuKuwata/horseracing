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
from ..selection import select_prediction_run

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")


def _f(x):
    return float(x) if x is not None else None


def _selection_numbers(selection) -> list[int] | None:
    """Normalise a persisted selection to [horse_number, ...] (Feature 045).

    Exotic rows store a list[int] already. Win rows (007) store a dict
    {"horse_id", "horse_number"} → [horse_number]; a win row without a horse_number
    cannot be displayed → None (caller drops it).
    """
    if isinstance(selection, dict):
        n = selection.get("horse_number")
        return [int(n)] if n is not None else None
    return [int(i) for i in selection]


@router.get("/races/{race_id}/recommendations", response_model=RecommendationResponse,
            tags=["recommendations"])
def recommendations(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return JSONResponse(status_code=422, content={
            "status": 422, "code": "invalid_race_id", "detail": "race_id must be 12 digits"})
    if get_race(session, race_id) is None:
        return JSONResponse(status_code=404, content={
            "status": 404, "code": "race_not_found", "detail": f"race {race_id} not found"})
    # Feature 043: scope to the SAME run the predictions view shows (active→latest), so append-only
    # re-generations and older runs never appear as duplicates. No run → typed-empty.
    run = select_prediction_run(session, race_id)
    run_id = run.prediction_run_id if run is not None else None
    items = []
    for r in exotic_recommendations(session, race_id, prediction_run_id=run_id):
        sel = _selection_numbers(r.selection)
        if sel is None:  # win row without a horse_number — not displayable
            continue
        items.append(RecommendationRow(
            recommendation_id=str(r.recommendation_id),
            bet_type=r.bet_type, selection=sel,
            stake_fraction=_f(r.stake_fraction),
            market_odds_used=_f(r.market_odds_used),
            estimated_market_odds_used=_f(r.estimated_market_odds_used),
            is_estimated_odds=r.is_estimated_odds, pseudo_odds=_f(r.pseudo_odds),
            pseudo_roi=_f(r.pseudo_roi), double_pseudo=bool(r.is_estimated_odds),
            logic_version=r.logic_version, computed_at=r.computed_at,
            prediction_run_id=str(r.prediction_run_id),
        ))
    return RecommendationResponse(race_id=race_id, items=items)
