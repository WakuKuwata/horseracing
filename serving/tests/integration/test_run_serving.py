"""US1 (SC-001/SC-005): run_serving persists prediction_runs/race_predictions/feature_snapshots."""

from __future__ import annotations

import pytest
from horseracing_db.models import FeatureSnapshot, PredictionRun, RacePrediction
from sqlalchemy import select

from horseracing_serving.pipeline import run_serving
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def test_run_serving_persists_all_three_tables(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    results = run_serving(session, race_id=_RACE, model_version=mv)
    assert len(results) == 1
    run_id = results[0].prediction_run_id

    run = session.get(PredictionRun, run_id)
    assert run.race_id == _RACE and run.model_version == mv
    assert run.logic_version.startswith("feat=") and ";serve=" in run.logic_version  # FR-014

    rps = session.scalars(
        select(RacePrediction).where(RacePrediction.prediction_run_id == run_id)
    ).all()
    assert len(rps) == 8  # all started horses, no gaps
    for rp in rps:
        assert 0 <= rp.win_prob <= rp.top2_prob <= rp.top3_prob <= 1  # PROB_MONOTONIC

    fss = session.scalars(
        select(FeatureSnapshot).where(FeatureSnapshot.prediction_run_id == run_id)
    ).all()
    assert len(fss) == 8
    assert fss[0].feature_version == "features-004"
    assert "_raw_win" in fss[0].features and "_calibrated_win" in fss[0].features
