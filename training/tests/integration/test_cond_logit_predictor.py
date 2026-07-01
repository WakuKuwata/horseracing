"""Feature 039 US2/US3: cond_logit LightGBMPredictor integration (consistency + leak + 009)."""

from __future__ import annotations

import dataclasses

import pytest
from horseracing_eval.consistency import check_consistency
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.predictor import ResultMarket

from horseracing_training import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def _fit_cond_logit(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    races = load_eval_races(session)
    predictor = LightGBMPredictor(
        session, seed=42, objective="cond_logit", calibration="isotonic"
    )
    predictor.fit([er.context for er in races])
    return predictor, races


def test_cond_logit_predict_race_is_consistent(session):
    # US2: softmax -> calibrate -> 009 yields a consistency-passing race prediction (Σwin=1)
    predictor, races = _fit_cond_logit(session)
    race = races[-1].context
    preds = predictor.predict_race(race)
    check_consistency(preds)  # 0<=win<=top2<=top3<=1, race sums within tolerance
    assert abs(sum(p.win for p in preds.values()) - 1.0) < 1e-9
    assert set(preds) == {h.horse_id for h in race.started_horses}
    assert predictor.fit_info_["objective"] == "cond_logit"
    assert predictor.fit_info_["postprocess"] == "group_softmax"


def test_cond_logit_invariant_to_result_market(session):
    # US3 leak: mutating result-time odds/popularity must not change cond_logit predictions
    predictor, races = _fit_cond_logit(session)
    race = races[-1].context
    preds_a = predictor.predict_race(race)
    mutated = tuple(
        dataclasses.replace(h, result_market=ResultMarket(odds=1.01, popularity=1))
        for h in race.started_horses
    )
    preds_b = predictor.predict_race(dataclasses.replace(race, started_horses=mutated))
    assert all(preds_a[k] == preds_b[k] for k in preds_a)
