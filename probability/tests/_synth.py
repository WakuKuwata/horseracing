"""Seed a race with predictions (win_prob) + results, for calibration/CLI integration tests."""

from __future__ import annotations

import datetime
from decimal import Decimal

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import (
    Horse,
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
)
from sqlalchemy.orm import Session


def seed_predicted_race(
    session: Session,
    *,
    race_id: str,
    win_probs: dict[str, float],
    finish: dict[str, int],
    race_date: datetime.date = datetime.date(2008, 6, 1),
    model_version: str = "m1",
):
    """Race + started horses + a prediction_run(win_prob) + finished results. Returns run id."""
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
                       venue_code=race_id[4:6]))
    from horseracing_db.models import ModelVersion
    session.merge(ModelVersion(model_version=model_version, model_family="test"))
    for hid in win_probs:
        session.merge(Horse(horse_id=hid, horse_name=hid))
    session.flush()
    for hid in win_probs:
        session.add(RaceHorse(race_id=race_id, horse_id=hid, horse_number=finish.get(hid),
                              entry_status=EntryStatus.STARTED))
        if hid in finish:
            session.add(RaceResult(race_id=race_id, horse_id=hid, finish_order=finish[hid],
                                   result_status=ResultStatus.FINISHED))
    run = PredictionRun(race_id=race_id, model_version=model_version, logic_version="v1")
    session.add(run)
    session.flush()
    for hid, wp in win_probs.items():
        d = Decimal(str(wp))
        session.add(RacePrediction(prediction_run_id=run.prediction_run_id, horse_id=hid,
                                   win_prob=d, top2_prob=d, top3_prob=d))
    session.commit()
    return run.prediction_run_id
