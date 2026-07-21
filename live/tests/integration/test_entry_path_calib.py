"""Feature 076 US2 (T018): live `refresh` threads manifest activation through BOTH stages.

Entry-path consistency (SC-011): the serving prediction stage and the betting recommendation stage
resolve the SAME ``manifest_digest`` and stamp it into their logic_version. Load-once (FR-018): the
manifest is loaded a small CONSTANT number of times per invocation (preflight + one per stage), never
once per race. Preflight fail-closed (T018): a bad manifest aborts the whole refresh before any write.
"""

from __future__ import annotations

import datetime
import json

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import PredictionRun, Recommendation
from horseracing_probability.calib_manifest import build_manifest
from sqlalchemy import func, select

from horseracing_live.orchestrate import refresh_range
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_MODEL = "lgbm-063"


def _manifest(tmp_path, *, fit_through="2007-12-31", scope="production", eligible=True) -> tuple:
    dig = "a" * 64
    m = build_manifest(
        attestation_digest=dig, bundle_digest="b" * 64,
        evaluation={"evaluation_contract_version": "v2", "base_model_version": _MODEL,
                    "attestation_digest": dig, "bundle_digest": "b" * 64, "verdict": "ADOPT"},
        stages=["model_win", "two_gamma_win", "stage_discount_top2", "stage_discount_top3"],
        code_sha="076cafe", seed=7, num_threads=1,
        two_gamma_lambda_full_precision={
            "two_gamma": {"gamma_lo": 1.6, "gamma_hi": 0.5, "pivot": 0.15},
            "stage_lambdas": {"top2": 0.82, "top3": 0.70}},
        fit_through=fit_through, artifact_scope=scope, activation_eligible=eligible)
    p = (tmp_path / "m.json").resolve()
    p.write_text(json.dumps(m), encoding="utf-8")
    return str(p), m["manifest_digest"][:12]


def _seed(session, tmp_path):
    # seed_learnable already sets both win odds AND finish_order → predictions + recs both have data
    seed_learnable(session, years=(2007, 2008), races_per_year=6, field_size=8)
    return make_active_model(session, tmp_path, model_version=_MODEL)


def test_refresh_both_stages_resolve_the_same_digest(session, tmp_path):
    _seed(session, tmp_path)
    path, digest12 = _manifest(tmp_path)
    rep = refresh_range(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        force=True, calib_manifest=path, calib_mode="manifest-required")
    assert rep.predict_error is None, rep.predict_error
    assert rep.recommend_error is None, rep.recommend_error

    # serving prediction runs carry the manifest digest...
    pred_lvs = session.scalars(
        select(PredictionRun.logic_version).where(PredictionRun.logic_version.contains(";calib="))
    ).all()
    assert pred_lvs and all(f";calib={digest12};" in lv for lv in pred_lvs)
    # ...and so do the betting recommendations — the SAME digest (SC-011)
    rec_lvs = session.scalars(
        select(Recommendation.logic_version)
        .where(Recommendation.bet_type == BetType.WIN)
        .where(Recommendation.logic_version.contains(";calib="))
    ).all()
    assert rec_lvs and all(f";calib={digest12};" in lv for lv in rec_lvs)


def test_manifest_loaded_a_constant_number_of_times_not_per_race(session, tmp_path, monkeypatch):
    """FR-018: load-once. The call count is preflight + one per stage — independent of race count."""
    _seed(session, tmp_path)
    path, _ = _manifest(tmp_path)
    import horseracing_probability.calib_activation as ca

    calls = {"n": 0}
    real = ca.load_calibration

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    # function-local ``from ...calib_activation import load_calibration`` resolves against this module
    # at call time, so patching the source is enough for every caller (preflight/serving/betting).
    monkeypatch.setattr(ca, "load_calibration", counting)
    refresh_range(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        force=True, calib_manifest=path, calib_mode="manifest-required")
    # preflight(1) + serving backfill pre-loop(1) + betting backfill pre-loop(1) = 3, NOT ~12 races
    assert calls["n"] <= 3, f"manifest loaded {calls['n']} times — should be load-once per stage"


def test_refresh_preflight_fails_closed_before_any_write(session, tmp_path):
    """T018: a bad (fixture-scoped) manifest aborts the whole refresh — no predictions, no recs."""
    from horseracing_probability.calib_activation import ActivationError
    _seed(session, tmp_path)
    bad, _ = _manifest(tmp_path, scope="fixture", eligible=False)
    with pytest.raises(ActivationError):
        refresh_range(
            session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
            force=True, calib_manifest=bad, calib_mode="manifest-required")
    # preflight ran BEFORE either stage → nothing calib-tagged was written
    assert session.scalar(
        select(func.count()).select_from(Recommendation)
        .where(Recommendation.logic_version.contains(";calib="))) == 0


# --- 076-gap (codex): live-serve + collect-prospective thread the manifest -------------------

def test_live_serve_manifest_threads_both_stages(session, tmp_path):
    """076-gap: live-serve manifest mode → prediction carries the stage-λ digest AND the
    recommendation carries the two-gamma digest (both from the manifest)."""
    from horseracing_live import live_serve
    from tests._synth import seed_pending_race
    seed_learnable(session, years=(2007, 2008), races_per_year=6, field_size=8)
    make_active_model(session, tmp_path, model_version=_MODEL)
    pending = "200806019911"
    seed_pending_race(session, race_id=pending, race_date=datetime.date(2008, 6, 1), field_size=8)
    path, digest12 = _manifest(tmp_path)

    rep = live_serve(session, race_id=pending, model_version=_MODEL,
                     calib_manifest=path, calib_mode="manifest-required")
    assert not rep.rejected, rep.reason
    assert rep.n_recommendations > 0
    pred = session.scalar(
        select(PredictionRun.logic_version).where(PredictionRun.race_id == pending))
    assert f";calib={digest12};" in pred  # serving stage-λ digest on the prediction run
    # the Kelly recommendation set (win + exotic) carries the two-gamma digest
    recs = session.scalars(
        select(Recommendation.logic_version)
        .where(~Recommendation.logic_version.contains(";prospective=1"))).all()
    assert recs and all(f";calib={digest12};" in lv for lv in recs)


def test_collect_prospective_manifest_two_gamma(session, tmp_path):
    """076-gap: prospective recommendations carry the manifest two-gamma digest."""
    from horseracing_db.enums import BetType
    from horseracing_live import live_serve
    from horseracing_live.orchestrate import collect_prospective
    from tests._synth import seed_pending_race
    seed_learnable(session, years=(2007, 2008), races_per_year=6, field_size=8)
    make_active_model(session, tmp_path, model_version=_MODEL)
    pending = "200806019911"
    seed_pending_race(session, race_id=pending, race_date=datetime.date(2008, 6, 1), field_size=8)
    live_serve(session, race_id=pending, model_version=_MODEL, recommend=False)  # create the run
    path, digest12 = _manifest(tmp_path)

    rep = collect_prospective(
        session, race_ids=[pending], scrape_fn=lambda s, r: datetime.datetime(
            2008, 6, 1, 9, 30, tzinfo=datetime.UTC),
        calib_manifest=path, calib_mode="manifest-required")
    assert rep.generated == 1
    wins = session.scalars(
        select(Recommendation.logic_version)
        .where(Recommendation.bet_type == BetType.WIN)
        .where(Recommendation.logic_version.contains(";prospective=1"))).all()
    assert wins and all(f";calib={digest12};" in lv for lv in wins)
