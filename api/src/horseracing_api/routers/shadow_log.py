"""shadow-log router (Feature 065): GET /shadow-log.

READ-ONLY prospective shadow-betting log roll-up. Aggregates PROSPECTIVE win recommendations ACROSS
RUNS (never active-run scoped), valued on the FROZEN market_odds_used via the pure win-only
predicate — the current race_horses.odds and favorite_realized are never read here (that would be
closing). Does not import horseracing_betting. Honest instrument: n_prospective=0 ⇒ still filling.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..backtest import shadow_log_summary
from ..deps import get_session
from ..queries import prospective_win_recommendations, race_finish_map
from ..schemas import ShadowLogMonth, ShadowLogResponse

router = APIRouter()


@router.get("/shadow-log", response_model=ShadowLogResponse, tags=["shadow-log"])
def shadow_log(session: Session = Depends(get_session)):
    recs = prospective_win_recommendations(session)
    finish: dict[str, tuple[dict, int]] = {}
    rows = []
    for r in recs:
        if r.race_id not in finish:
            finish[r.race_id] = race_finish_map(session, r.race_id)
        fmap, n_winners = finish[r.race_id]
        rows.append({
            "bet_type": r.bet_type.value if hasattr(r.bet_type, "value") else str(r.bet_type),
            "logic_version": r.logic_version,
            "selection": r.selection,
            "market_odds_used": (
                float(r.market_odds_used) if r.market_odds_used is not None else None
            ),
            "is_estimated_odds": r.is_estimated_odds,
            "estimated_market_odds_used": (
                float(r.estimated_market_odds_used)
                if r.estimated_market_odds_used is not None else None
            ),
            "computed_at": r.computed_at.isoformat() if r.computed_at is not None else None,
            "finish_map": fmap,
            "n_winners": n_winners,
        })
    s = shadow_log_summary(rows)
    return ShadowLogResponse(
        n_prospective=s.n_prospective, n_settled=s.n_settled, n_hit=s.n_hit,
        hit_rate=s.hit_rate, recovery_rate=s.recovery_rate, n_pending=s.n_pending,
        n_void=s.n_void, weak_pretime=s.weak_pretime, first_at=s.first_at, last_at=s.last_at,
        by_month=[ShadowLogMonth(**m) for m in s.by_month],
    )
