"""T012 (US3): live prediction p == retrospective run_serving p; prospective-injectable (SC-004/008)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import PredictionRun, RacePrediction
from horseracing_serving.pipeline import run_serving
from sqlalchemy import select

from horseracing_live import live_serve
from tests._synth import make_active_model, seed_learnable, seed_pending_race

pytestmark = pytest.mark.integration

_PENDING = "200806019902"


def _winprobs(session, run_id) -> dict:
    rows = session.execute(
        select(RacePrediction.horse_id, RacePrediction.win_prob)
        .where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {hid: round(float(wp), 12) for hid, wp in rows}


def test_live_equals_retrospective_prediction(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))

    live = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    retro = run_serving(session, race_id=_PENDING, model_version=mv)[0]

    # live path adds no leak/divergence: identical win_prob (parity is on p, NOT odds-dependent recs)
    assert _winprobs(session, live.prediction_run_id) == _winprobs(session, retro.prediction_run_id)


def test_prediction_run_is_backtest_injectable(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))
    rep = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    # the generated run is a standard prediction_run with model_version + race_predictions
    run = session.get(PredictionRun, rep.prediction_run_id)
    assert run is not None and run.model_version == mv and run.race_id == _PENDING
    assert _winprobs(session, rep.prediction_run_id)   # non-empty → consumable by 007/011/016 backtest
