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

from ..backtest import favorite_realized, win_realized
from ..deps import get_session
from ..queries import exotic_recommendations, get_race, race_finish_map, win_odds
from ..schemas import FavoriteBaseline, RecommendationResponse, RecommendationRow
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
    # Feature 049: load the race's official finishing map ONCE (empty ⇒ unsettled) for the
    # retrospective WIN backtest. Results are display-only and never re-enter model features (II).
    finish_map, n_winners = race_finish_map(session, race_id)
    rows = list(exotic_recommendations(session, race_id, prediction_run_id=run_id))
    items = []
    for r in rows:
        sel = _selection_numbers(r.selection)
        if sel is None:  # win row without a horse_number — not displayable
            continue
        # Feature 049: realised outcome is WIN-only (real single-win odds); non-win → all null.
        wr = (
            win_realized(r.selection, r.market_odds_used,
                         finish_map=finish_map, n_winners=n_winners)
            if r.bet_type == "win" else None
        )
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
            settled=wr.settled if wr is not None else False,
            hit=wr.hit if wr is not None else None,
            dead_heat=wr.dead_heat if wr is not None else False,
            realized_return=wr.realized_return if wr is not None else None,
            realized_roi=wr.realized_roi if wr is not None else None,
        ))
    # Feature 064: honest-display context (read-time; never re-enters model features, II).
    has_win = any(i.bet_type == "win" for i in items)
    if run_id is None:
        win_policy_status = "no_run"
    elif has_win:
        win_policy_status = "generated"
    elif rows:                       # some recs (exotic) but no win → win policy ran, selected none
        win_policy_status = "no_win_selected"
    else:
        win_policy_status = "not_generated"
    fav = favorite_realized(
        win_odds(session, race_id), finish_map=finish_map, n_winners=n_winners
    )
    favorite_baseline = FavoriteBaseline(
        horse_number=fav.horse_number, odds=fav.odds, settled=fav.settled, hit=fav.hit,
        dead_heat=fav.dead_heat, realized_return=fav.realized_return, realized_roi=fav.realized_roi,
    )
    return RecommendationResponse(
        race_id=race_id, items=items, win_policy_status=win_policy_status,
        favorite_baseline=favorite_baseline,
    )
