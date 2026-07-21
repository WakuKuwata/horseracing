"""Feature 076 US2 (T014-T017): serving stage-discount λ from the immutable manifest.

THE serving invariant: WIN (`race_predictions.win_prob`) is byte-identical whether or not a manifest
is used — stage discount only touches the DISPLAY top2/top3 (SC-006). Plus: manifest λ actually
changes top2/top3, the digest is recorded, bad manifests fail closed, and the leaky runtime fit is
never called in manifest mode.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import pytest
from horseracing_db.models import RacePrediction
from horseracing_probability.calib_manifest import build_manifest
from sqlalchemy import select

from horseracing_serving.pipeline import run_serving
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_MODEL = "lgbm-063"  # manifest base is hardcoded to lgbm-063 (074)


def _write_manifest(tmp_path: Path, *, fit_through="2007-12-31", scope="production", eligible=True,
                    top2=0.82, top3=0.70, name="manifest.json") -> str:
    dig = "a" * 64
    manifest = build_manifest(
        attestation_digest=dig, bundle_digest="b" * 64,
        evaluation={"evaluation_contract_version": "v2", "base_model_version": _MODEL,
                    "attestation_digest": dig, "bundle_digest": "b" * 64, "verdict": "ADOPT"},
        stages=["model_win", "two_gamma_win", "stage_discount_top2", "stage_discount_top3"],
        code_sha="076cafe", seed=7, num_threads=1,
        two_gamma_lambda_full_precision={
            "two_gamma": {"gamma_lo": 1.6, "gamma_hi": 0.5, "pivot": 0.15},
            "stage_lambdas": {"top2": top2, "top3": top3}},
        fit_through=fit_through, artifact_scope=scope, activation_eligible=eligible,
    )
    p = (tmp_path / name).resolve()
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return str(p)


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    return make_active_model(session, tmp_path, model_version=_MODEL)


def _preds(session, run_id):
    return {rp.horse_id: rp for rp in session.scalars(
        select(RacePrediction).where(RacePrediction.prediction_run_id == run_id))}


def test_win_is_byte_identical_regardless_of_manifest(session, tmp_path):
    """SC-006 (the serving invariant): stage discount never moves WIN — only top2/top3 display."""
    mv = _setup(session, tmp_path)
    # legacy path WITHOUT any discount = the untouched win baseline
    legacy = run_serving(session, race_id=_RACE, model_version=mv, apply_stage_discount=False)
    base_win = {h: rp.win_prob for h, rp in _preds(session, legacy[0].prediction_run_id).items()}

    path = _write_manifest(tmp_path)
    man = run_serving(session, race_id=_RACE, model_version=mv,
                      calib_manifest=path, calib_mode="manifest-required")
    man_preds = _preds(session, man[0].prediction_run_id)
    assert set(man_preds) == set(base_win)
    for h, rp in man_preds.items():
        assert rp.win_prob == base_win[h], f"WIN moved for {h} — stage discount leaked into win"


def test_manifest_lambda_changes_top2_top3_and_records_digest(session, tmp_path):
    """The λ from the manifest is actually applied to the derived top2/top3, and audited."""
    mv = _setup(session, tmp_path)
    plain = run_serving(session, race_id=_RACE, model_version=mv, apply_stage_discount=False)
    plain_top3 = {h: rp.top3_prob for h, rp in _preds(session, plain[0].prediction_run_id).items()}

    path = _write_manifest(tmp_path, top2=0.82, top3=0.70)
    man = run_serving(session, race_id=_RACE, model_version=mv,
                      calib_manifest=path, calib_mode="manifest-required")
    assert ";calib=" in man[0].logic_version and ";calibmode=manifest" in man[0].logic_version
    man_top3 = {h: rp.top3_prob for h, rp in _preds(session, man[0].prediction_run_id).items()}
    # a non-identity λ must move at least one top3 value away from the plain Harville derivation
    assert any(man_top3[h] != plain_top3[h] for h in plain_top3)


def test_manifest_mode_never_calls_the_runtime_stage_fit(session, tmp_path, monkeypatch):
    """FR-012/SC-009: manifest mode must not touch the non-OOS `fit_product_stage_discount`."""
    mv = _setup(session, tmp_path)
    path = _write_manifest(tmp_path)
    import horseracing_probability.model_calibration as mc

    def boom(*a, **k):
        raise AssertionError("leaky runtime stage-discount fit called in manifest mode")

    monkeypatch.setattr(mc, "fit_product_stage_discount", boom)
    res = run_serving(session, race_id=_RACE, model_version=mv,
                      calib_manifest=path, calib_mode="manifest-required")
    assert len(res) == 1  # produced purely from the manifest λ


def test_manifest_required_fail_closed_temporal(session, tmp_path):
    """FR-021: a manifest whose fit window covers the race day is rejected (no silent fallback)."""
    from horseracing_probability.calib_activation import ActivationError
    mv = _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2008-06-01")  # AFTER the 2008-01-01 race
    with pytest.raises(ActivationError):
        run_serving(session, race_id=_RACE, model_version=mv,
                    calib_manifest=path, calib_mode="manifest-required")


def test_manifest_required_fail_closed_scope(session, tmp_path):
    """SC-010: a fixture-scoped manifest is rejected by the production loader profile."""
    from horseracing_probability.calib_activation import ActivationError
    mv = _setup(session, tmp_path)
    path = _write_manifest(tmp_path, scope="fixture", eligible=False)
    with pytest.raises(ActivationError):
        run_serving(session, race_id=_RACE, model_version=mv,
                    calib_manifest=path, calib_mode="manifest-required")


def test_backfill_manifest_records_digest_and_win_parity(session, tmp_path):
    """Backfill: manifest λ over a range, digest recorded, and WIN still byte-identical."""
    from horseracing_serving.pipeline import run_serving_backfill
    mv = _setup(session, tmp_path)
    # baseline win via a no-discount single-race serve, then wipe so backfill regenerates
    legacy = run_serving(session, race_id=_RACE, model_version=mv, apply_stage_discount=False)
    base_win = {h: rp.win_prob for h, rp in _preds(session, legacy[0].prediction_run_id).items()}

    path = _write_manifest(tmp_path)
    counts = run_serving_backfill(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        model_version=mv, force=True, calib_manifest=path, calib_mode="manifest-required")
    assert counts.generated > 0
    from horseracing_db.models import PredictionRun
    man_run = session.scalars(
        select(PredictionRun)
        .where(PredictionRun.race_id == _RACE)
        .where(PredictionRun.logic_version.contains(";calib="))
        .order_by(PredictionRun.prediction_run_id.desc())
    ).first()
    assert man_run is not None, "expected a manifest-tagged run for the race"
    man = _preds(session, man_run.prediction_run_id)
    assert set(man) == set(base_win)
    for h, rp in man.items():
        assert rp.win_prob == base_win[h], "backfill stage discount leaked into win"


def test_backfill_generates_manifest_version_over_a_legacy_run(session, tmp_path):
    """076-gap (codex): manifest predict-backfill must NOT skip races that only have a LEGACY run.

    The idempotency skip is digest-aware, so a race already predicted in legacy mode still gets its
    manifest run generated (a bare (race, model) check would wrongly skip it)."""
    from horseracing_db.models import PredictionRun

    from horseracing_serving.pipeline import run_serving_backfill
    mv = _setup(session, tmp_path)
    # a legacy run exists for the race (NOT force) ...
    run_serving(session, race_id=_RACE, model_version=mv, apply_stage_discount=False)
    path = _write_manifest(tmp_path)
    # ... manifest backfill WITHOUT --force must still generate the manifest version (not skip)
    counts = run_serving_backfill(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        model_version=mv, force=False, calib_manifest=path, calib_mode="manifest-required")
    assert counts.generated >= 1
    man = session.scalars(
        select(PredictionRun).where(PredictionRun.race_id == _RACE)
        .where(PredictionRun.logic_version.contains(";calib="))).all()
    assert man, "the manifest run was skipped — digest-aware idempotency regressed"


def test_cli_validation(session, tmp_path):
    """cli-contract: relative path / mode↔manifest contradiction are typed exits."""
    from horseracing_serving.cli import _validate_calib
    with pytest.raises(SystemExit):
        _validate_calib(argparse.Namespace(calib_mode="manifest-required", calib_manifest=None))
    with pytest.raises(SystemExit):
        _validate_calib(argparse.Namespace(calib_mode="legacy-runtime", calib_manifest="/abs/m.json"))
    with pytest.raises(SystemExit):
        _validate_calib(argparse.Namespace(calib_mode="manifest-required", calib_manifest="rel.json"))
