"""US1 (FR-004): the predictor never reads result-time market data (leak check)."""

from __future__ import annotations

import dataclasses

import pytest
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.predictor import ResultMarket

from horseracing_training import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def test_predictions_are_invariant_to_result_market(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    races = load_eval_races(session)

    predictor = LightGBMPredictor(session, seed=42)
    predictor.fit([er.context for er in races])
    assert LightGBMPredictor.is_leaky_reference is False

    race = races[-1].context
    preds_a = predictor.predict_race(race)

    # Replace every horse's result_market with an extreme value. A leaky predictor would
    # shift; ours must produce byte-identical predictions.
    mutated = tuple(
        dataclasses.replace(h, result_market=ResultMarket(odds=1.01, popularity=1))
        for h in race.started_horses
    )
    preds_b = predictor.predict_race(dataclasses.replace(race, started_horses=mutated))

    assert preds_a.keys() == preds_b.keys()
    assert all(preds_a[k] == preds_b[k] for k in preds_a)
    # every started horse received a prediction (no silent drops)
    assert set(preds_a) == {h.horse_id for h in race.started_horses}
