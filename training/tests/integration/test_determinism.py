"""Polish (SC-006): same data + same fold + same seed -> identical metrics & predictions."""

from __future__ import annotations

import pytest
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate

from horseracing_training import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def test_two_runs_produce_identical_metrics(session):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=10, field_size=8)
    races = load_eval_races(session)

    r1 = evaluate(LightGBMPredictor(session, seed=42), races, first_valid_year=2008)
    r2 = evaluate(LightGBMPredictor(session, seed=42), races, first_valid_year=2008)
    assert r1.to_summary() == r2.to_summary()


def test_two_fits_produce_identical_predictions(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    races = load_eval_races(session)
    train_ctx = [er.context for er in races]
    target = races[-1].context

    p1 = LightGBMPredictor(session, seed=42)
    p1.fit(train_ctx)
    p2 = LightGBMPredictor(session, seed=42)
    p2.fit(train_ctx)

    preds1, preds2 = p1.predict_race(target), p2.predict_race(target)
    assert preds1 == preds2
