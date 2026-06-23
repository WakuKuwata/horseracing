"""Period pseudo-ROI backtest (contracts/backtest.md, R8).

Loads the serving model once, builds the as-of feature matrix once, and computes win prob per
race IN-MEMORY via serving.predict_race (no prediction_runs are persisted). The same evaluated
race set is scored for the EV strategy and both ROI baselines. If the period overlaps the model's
train_through, the run is flagged ``in_sample`` (the model saw that period — pseudo metrics are
optimistic; serving has no train_through guard).
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import ResultStatus
from horseracing_db.models import Race, RaceHorse, RaceResult
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.predictor import predict_race
from sqlalchemy import select
from sqlalchemy.orm import Session

from .roi import RaceOutcome, RoiReport, score_backtest
from .strategies import EVStrategy, FavoriteROIBaseline, UniformROIBaseline


def _is_in_sample(metadata: dict, start_date: datetime.date) -> bool:
    tt = metadata.get("train_through")
    if not tt:
        return False
    try:
        return start_date <= datetime.date.fromisoformat(str(tt))
    except ValueError:
        return False


def _winners(session: Session, race_id: str) -> set[str]:
    return set(
        session.scalars(
            select(RaceResult.horse_id)
            .where(RaceResult.race_id == race_id)
            .where(RaceResult.result_status == ResultStatus.FINISHED)
            .where(RaceResult.finish_order == 1)
        )
    )


def _race_outcomes(session: Session, model, start_date, end_date) -> list[RaceOutcome]:
    feature_rows = build_feature_matrix(session, end_date=end_date)
    present = set(feature_rows["race_id"].unique())
    races = session.execute(
        select(Race.race_id)
        .where(Race.race_date >= start_date)
        .where(Race.race_date <= end_date)
        .order_by(Race.race_id)
    ).all()

    outcomes: list[RaceOutcome] = []
    for (race_id,) in races:
        if race_id not in present:
            continue
        preds, _ = predict_race(model, race_id, feature_rows)
        if not preds:
            continue
        rhs = session.scalars(select(RaceHorse).where(RaceHorse.race_id == race_id)).all()
        horses = [
            {
                "horse_id": rh.horse_id,
                "horse_number": rh.horse_number,
                "win_prob": preds[rh.horse_id].win if rh.horse_id in preds else None,
                "odds": float(rh.odds) if rh.odds is not None else None,
                "entry_status": rh.entry_status,
            }
            for rh in rhs
        ]
        outcomes.append(
            RaceOutcome(race_id=race_id, horses=horses, winners=_winners(session, race_id))
        )
    return outcomes


def run_backtest(
    session: Session,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    model_version: str | None = None,
    threshold: float = 1.0,
    stake: float = 100.0,
) -> dict[str, RoiReport]:
    model = load_serving_model(session, model_version)
    in_sample = _is_in_sample(model.metadata, start_date)
    outcomes = _race_outcomes(session, model, start_date, end_date)

    strategies = [EVStrategy(threshold), FavoriteROIBaseline(), UniformROIBaseline()]
    return {
        s.name: score_backtest(outcomes, s, stake=stake, in_sample=in_sample)
        for s in strategies
    }
