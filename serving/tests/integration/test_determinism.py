"""US2 (SC-002): same race+model+logic -> identical race_predictions; append-only 2 runs."""

from __future__ import annotations

import pytest
from horseracing_db.models import PredictionRun, RacePrediction
from sqlalchemy import func, select

from horseracing_serving.pipeline import run_serving
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def _preds(session, run_id):
    rps = session.scalars(
        select(RacePrediction).where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {rp.horse_id: (rp.win_prob, rp.top2_prob, rp.top3_prob) for rp in rps}


def test_two_runs_identical_and_appended(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    r1 = run_serving(session, race_id=_RACE, model_version=mv)[0]
    r2 = run_serving(session, race_id=_RACE, model_version=mv)[0]

    assert r1.prediction_run_id != r2.prediction_run_id  # append-only
    assert _preds(session, r1.prediction_run_id) == _preds(session, r2.prediction_run_id)

    n_runs = session.scalar(
        select(func.count()).select_from(PredictionRun).where(PredictionRun.race_id == _RACE)
    )
    assert n_runs == 2
