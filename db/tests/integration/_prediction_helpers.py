"""Shared scaffolding for US4 prediction/recommendation tests (not collected)."""

from __future__ import annotations

import datetime

from horseracing_db.models import Horse, ModelVersion, PredictionRun, Race, RaceHorse

RACE_ID = "202705021101"


def setup_run(session, *, horse_id="H1", model_version="m-001", odds=3.0):
    session.add(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2027, 5, 1)))
    session.add(Horse(horse_id=horse_id))
    session.add(RaceHorse(race_id=RACE_ID, horse_id=horse_id, odds=odds))
    session.add(ModelVersion(model_version=model_version, model_family="lightgbm"))
    session.flush()

    run = PredictionRun(race_id=RACE_ID, model_version=model_version, logic_version="v1")
    session.add(run)
    session.flush()
    session.refresh(run)
    return run
