"""Generate single-win EV recommendations from a prediction run (contracts/recommend.md).

Reads the run's race_predictions (win prob) joined with race_horses (odds / horse_number /
entry_status), selects EV>=threshold bets (renormalized over started), and appends
recommendations rows. Append-only: re-running adds a new group (distinguished by logic_version).
Bet selection never reads race_results (leak boundary).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from horseracing_db.enums import BetType
from horseracing_db.models import PredictionRun, RaceHorse, RacePrediction, Recommendation
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import BETTING_LOGIC_VERSION
from .ev import select_ev_bets

DEFAULT_THRESHOLD = 1.0
DEFAULT_STAKE = 100.0


def default_logic_version(threshold: float, stake: float) -> str:
    return (
        f"ev=win_prob*odds;thr={threshold};stake={stake};"
        f"excl=scratch+nullodds+zeroprob;renorm=started;v={BETTING_LOGIC_VERSION}"
    )


def _load_horses(session: Session, prediction_run_id, race_id: str) -> list[dict]:
    probs = dict(
        session.execute(
            select(RacePrediction.horse_id, RacePrediction.win_prob).where(
                RacePrediction.prediction_run_id == prediction_run_id
            )
        ).all()
    )
    horses: list[dict] = []
    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == race_id)):
        wp = probs.get(rh.horse_id)
        horses.append(
            {
                "horse_id": rh.horse_id,
                "horse_number": rh.horse_number,
                "win_prob": float(wp) if wp is not None else None,
                "odds": float(rh.odds) if rh.odds is not None else None,
                "entry_status": rh.entry_status,
            }
        )
    return horses


def generate_recommendations(
    session: Session,
    *,
    prediction_run_id,
    threshold: float = DEFAULT_THRESHOLD,
    stake: float = DEFAULT_STAKE,
    logic_version: str | None = None,
) -> list[uuid.UUID]:
    run = session.get(PredictionRun, prediction_run_id)
    if run is None:
        raise ValueError(f"prediction_run {prediction_run_id} not found")
    lv = logic_version or default_logic_version(threshold, stake)

    horses = _load_horses(session, prediction_run_id, run.race_id)
    bets = select_ev_bets(horses, threshold=threshold, stake=stake)

    ids: list[uuid.UUID] = []
    for b in bets:
        rec = Recommendation(
            prediction_run_id=prediction_run_id,
            race_id=run.race_id,
            bet_type=BetType.WIN,
            selection={"horse_id": b.horse_id, "horse_number": b.horse_number},
            market_odds_used=Decimal(str(b.odds)),
            estimated_market_odds_used=None,
            is_estimated_odds=False,
            pseudo_odds=Decimal(str(1.0 / b.win_prob)),     # model-implied odds
            pseudo_roi=Decimal(str(b.ev - 1.0)),            # decision-time expected ROI
            logic_version=lv,
        )
        session.add(rec)
        session.flush()
        ids.append(rec.recommendation_id)
    session.commit()
    return ids
