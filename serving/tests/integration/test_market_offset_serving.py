"""Feature 060: market-offset model serving — load/lv audit/typed skip (INV-M4/M6, SC-003)."""

from __future__ import annotations

import pytest
from horseracing_db.models import RaceHorse
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from horseracing_features.registry import FEATURE_VERSION
from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import save_model_version
from horseracing_training.predictor import LightGBMPredictor
from sqlalchemy import select

from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.pipeline import MarketOffsetSkip, run_serving, run_serving_backfill
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def _make_market_model(session, artifacts_root, model_version="mkt-test") -> str:
    """Train a pl_topk + market-offset model on the synth data, register as CANDIDATE."""
    races = load_eval_races(session)
    evald = LightGBMPredictor(session, seed=42, objective="pl_topk", market_offset=True)
    result = evaluate(evald, races, first_valid_year=2008)
    final = LightGBMPredictor(session, seed=42, objective="pl_topk", market_offset=True)
    final.fit([er.context for er in races])
    save_model_version(
        session, model_version=model_version, predictor=final, eval_result=result,
        decision=AdoptionDecision(adopted=False, reasons={"feature": "060"}),
        gate=AdoptionGate(ece_threshold=0.0), artifacts_root=str(artifacts_root),
        feature_version=FEATURE_VERSION, git_sha=None, register_as_candidate=True,
    )
    return model_version


def test_market_offset_model_serves_with_audit_marker(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = _make_market_model(session, tmp_path)

    model = load_serving_model(session, mv)
    assert model.market_offset is not None
    assert model.market_offset["kind"] == "log_q_devig"

    results = run_serving(session, race_id=_RACE, model_version=mv)
    assert len(results) == 1
    assert ";mkt=logq" in results[0].logic_version  # INV-M6
    assert results[0].n_horses == 8


def test_market_offset_typed_skip_on_missing_odds(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = _make_market_model(session, tmp_path)

    # poison ONE started horse's odds in the target race -> whole race must be skipped
    rh = session.scalars(
        select(RaceHorse).where(RaceHorse.race_id == _RACE).limit(1)
    ).first()
    rh.odds = None
    session.commit()

    with pytest.raises(MarketOffsetSkip):  # single-race mode surfaces the typed error
        run_serving(session, race_id=_RACE, model_version=mv)

    # backfill counts it as skip_no_odds and continues (no prediction row for the race)
    import datetime

    counts = run_serving_backfill(
        session, date_from=datetime.date(2008, 1, 2), date_to=datetime.date(2008, 1, 2),
        model_version=mv,
    )
    assert counts.skip_no_odds == 1
    assert counts.generated == 0


def test_ordinary_model_stays_unchanged(session, tmp_path):
    """INV-M3: a non-offset model loads with market_offset=None and no mkt marker."""
    from tests._synth import make_active_model

    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    model = load_serving_model(session, mv)
    assert model.market_offset is None
    results = run_serving(session, race_id=_RACE, model_version=mv)
    assert ";mkt=" not in results[0].logic_version
