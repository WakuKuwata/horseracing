"""US2 (SC-004/SC-003): predictions are invariant to result-derived data and future races."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceHorse, RacePrediction, RaceResult
from sqlalchemy import select, update

from horseracing_serving.pipeline import run_serving
from tests._synth import insert_race, make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def _preds(session, run_id):
    rps = session.scalars(
        select(RacePrediction).where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {rp.horse_id: (rp.win_prob, rp.top2_prob, rp.top3_prob) for rp in rps}


def test_invariant_to_results_odds_and_future(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    before = _preds(session, run_serving(session, race_id=_RACE, model_version=mv)[0].prediction_run_id)

    # mutate result-derived info for the target race + add future races/results
    session.execute(update(RaceResult).where(RaceResult.race_id == _RACE).values(finish_order=99))
    session.execute(
        update(RaceHorse).where(RaceHorse.race_id == _RACE).values(odds=1.01, popularity=1)
    )
    insert_race(
        session, race_id="200901010101", race_date=datetime.date(2009, 1, 2),
        horses=[{"horse_id": "FUT1", "horse_number": 1, "finish_order": 1,
                 "result_status": ResultStatus.FINISHED}],
    )
    session.commit()

    after = _preds(session, run_serving(session, race_id=_RACE, model_version=mv)[0].prediction_run_id)
    assert before == after  # no result-derived input, no future leak
