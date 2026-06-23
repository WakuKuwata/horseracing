"""US1/US2 (SC-003, SC-002 end-to-end): walk-forward eval beats uniform; calibrator is
train-only (mutating valid-year results does not change the train-fit calibrator)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import RaceResult
from horseracing_eval.baselines import UniformBaseline
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from sqlalchemy import update

from horseracing_training import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def test_walkforward_beats_uniform_on_win_logloss(session):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=12, field_size=8)
    races = load_eval_races(session)

    model = evaluate(LightGBMPredictor(session, seed=42), races, first_valid_year=2008)
    uniform = evaluate(UniformBaseline(), races, first_valid_year=2008)

    assert model.overall["win"]["log_loss"] < uniform.overall["win"]["log_loss"]


def test_calibrator_is_unchanged_by_valid_period_results(session):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=10, field_size=8)
    races = load_eval_races(session)
    train_ctx = [er.context for er in races if er.context.race_date.year < 2009]

    p_before = LightGBMPredictor(session, seed=42)
    p_before.fit(train_ctx)
    params_before = p_before.calibrator_.params_dict()

    # Drastically rewrite the 2009 (valid) results: no winners at all.
    session.execute(
        update(RaceResult)
        .where(RaceResult.race_id.like("2009%"))
        .values(finish_order=99, result_status=ResultStatus.FINISHED)
    )
    session.commit()

    p_after = LightGBMPredictor(session, seed=42)
    p_after.fit(train_ctx)
    params_after = p_after.calibrator_.params_dict()

    assert params_before == params_after  # valid-period labels never touched the calibrator
