"""Feature 076 US1 (T011): betting two-gamma read from the immutable manifest, not the runtime fit.

Parity: the γ the recommendation uses == the manifest γ (SC-002; the numeric proof that the
FULL-precision γ is actually applied lives in probability's loader unit tests). legacy-runtime stays
the default byte-path, and a run holds exactly ONE calibration mode (mixing is refused, codex P0-1).
Fail-closed: a bad ``manifest-required`` manifest is an ERROR (exit!=0), never a silent legacy
fallback (SC-005/FR-005).
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import pytest
from horseracing_db.models import Recommendation
from horseracing_probability.calib_manifest import build_manifest
from sqlalchemy import func, select

from horseracing_betting.cli import _cmd_recommend_serve
from horseracing_betting.exotic_ev import candidate_bets, canonical_field
from horseracing_betting.exotic_recommend import _load_field_inputs
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_MODEL = "lgbm-063"  # manifest base is hardcoded to lgbm-063 (074) — the run must match it
_GAMMA_LO = 1.6543210987654321
_GAMMA_HI = 0.5123456789012345


def _write_manifest(
    tmp_path: Path, *, fit_through: str, scope="production", eligible=True,
    gamma_lo: float = _GAMMA_LO, gamma_hi: float = _GAMMA_HI, name: str = "manifest.json",
) -> str:
    dig = "a" * 64
    manifest = build_manifest(
        attestation_digest=dig, bundle_digest="b" * 64,
        evaluation={"evaluation_contract_version": "v2", "base_model_version": _MODEL,
                    "attestation_digest": dig, "bundle_digest": "b" * 64, "verdict": "ADOPT"},
        stages=["model_win", "two_gamma_win", "stage_discount_top2", "stage_discount_top3"],
        code_sha="076cafe", seed=7, num_threads=1,
        two_gamma_lambda_full_precision={
            "two_gamma": {"gamma_lo": gamma_lo, "gamma_hi": gamma_hi, "pivot": 0.15},
            "stage_lambdas": {"top2": 0.82, "top3": 0.70}},
        fit_through=fit_through, artifact_scope=scope, activation_eligible=eligible,
    )
    p = (tmp_path / name).resolve()
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return str(p)


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path, model_version=_MODEL)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)
    preds, odds, scr, n2id = _load_field_inputs(session, run_id, _RACE)
    field = canonical_field(_RACE, preds, odds, scratched=scr, number_to_id=n2id)
    for bets in candidate_bets(field).values():
        for b in bets:
            from horseracing_db.models import ExoticOdds
            session.add(ExoticOdds(race_id=_RACE, bet_type=b.bet_type, selection=list(b.selection),
                                   odds=round(1.5 / b.p_model, 4), coverage_scope="full"))
    session.commit()
    return run_id


def _ns(**kw):
    base = dict(race_id=_RACE, stage_discount=False, win_odds_cap=None,
               calib_manifest=None, calib_mode="legacy-runtime", database_url=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _win_lv(session, run_id) -> str:
    from horseracing_db.enums import BetType
    return session.scalar(
        select(Recommendation.logic_version)
        .where(Recommendation.prediction_run_id == run_id)
        .where(Recommendation.bet_type == BetType.WIN)
    )


def _count(session, run_id) -> int:
    return session.scalar(select(func.count()).select_from(Recommendation)
                          .where(Recommendation.prediction_run_id == run_id))


def test_manifest_two_gamma_parity_and_audit(session, tmp_path, capsys):
    """SC-002/006: the recommendation carries the MANIFEST γ + ``;calib=<digest>`` provenance."""
    run_id = _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2007-12-31")
    rc = _cmd_recommend_serve(session, _ns(calib_manifest=path, calib_mode="manifest-required"))
    assert rc == 0
    assert capsys.readouterr().out.startswith("OK:")
    lv = _win_lv(session, run_id)
    assert lv is not None
    # parity: the manifest's γ_lo (formatted %.5f) flows into the applied calibrator (SC-002)
    assert f"gamma_lo={_GAMMA_LO:.5f}" in lv
    assert f"gamma_hi={_GAMMA_HI:.5f}" in lv
    # audit: content-addressed digest recorded (FR-009)
    assert ";calib=" in lv and ";calibmode=manifest" in lv


def test_mixing_calibration_modes_on_one_run_is_refused(session, tmp_path, capsys):
    """codex P0-1: the read API returns EVERY row of a run, so a run must hold ONE calibration mode.

    Legacy and manifest groups are distinguished by logic_version, but they are groups WITHIN one
    prediction_run — not separate runs. Generating the second mode would display conflicting bets
    side by side, so it is refused rather than silently created.
    """
    run_id = _setup(session, tmp_path)
    assert _cmd_recommend_serve(session, _ns()) == 0  # legacy first
    capsys.readouterr()
    n_legacy = _count(session, run_id)
    assert n_legacy > 0
    assert ";calib=" not in _win_lv(session, run_id)  # legacy carries NO manifest digest

    path = _write_manifest(tmp_path, fit_through="2007-12-31")
    rc = _cmd_recommend_serve(
        session, _ns(calib_manifest=path, calib_mode="manifest-required"))
    assert rc == 1
    assert "different calibration" in capsys.readouterr().out
    assert _count(session, run_id) == n_legacy  # nothing added — the run stays single-mode


def test_manifest_required_fail_closed_temporal(session, tmp_path, capsys):
    """SC-005/FR-021: a manifest whose fit window covers the race is ERROR, not silent fallback."""
    run_id = _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2008-06-01")  # AFTER the 2008-01-01 race
    rc = _cmd_recommend_serve(session, _ns(calib_manifest=path, calib_mode="manifest-required"))
    assert rc == 1
    assert "ERROR" in capsys.readouterr().out
    assert _count(session, run_id) == 0  # nothing written


def test_manifest_required_fail_closed_scope(session, tmp_path, capsys):
    """SC-010: a fixture-scoped manifest is rejected by the production loader profile."""
    run_id = _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2007-12-31", scope="fixture", eligible=False)
    rc = _cmd_recommend_serve(session, _ns(calib_manifest=path, calib_mode="manifest-required"))
    assert rc == 1
    assert _count(session, run_id) == 0


def test_manifest_mode_never_calls_the_leaky_runtime_loader(session, tmp_path, monkeypatch):
    """THE point of 076 (FR-012/SC-009): manifest mode must not touch the non-OOS runtime fit.

    ``load_p_samples``/``_latest_run_predictions`` read the latest persisted predictions (full-history
    model, target race included) — the leak 074 documented. In manifest mode that path must be dead.
    """
    _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2007-12-31")
    import horseracing_probability.model_calibration as mc

    def boom(*a, **k):
        raise AssertionError("leaky runtime calibration path called in manifest mode")

    monkeypatch.setattr(mc, "load_p_samples", boom)
    monkeypatch.setattr(mc, "fit_p_calibrator", boom)
    monkeypatch.setattr(mc, "_latest_run_predictions", boom)
    monkeypatch.setattr(mc, "fit_product_stage_discount", boom)
    rc = _cmd_recommend_serve(
        session, _ns(calib_manifest=path, calib_mode="manifest-required", stage_discount=True))
    assert rc == 0  # generated purely from the manifest


def test_same_manifest_rerun_is_idempotent(session, tmp_path, capsys):
    """FR-010: re-running the SAME manifest adds no rows (a different digest is a different group)."""
    run_id = _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2007-12-31")
    assert _cmd_recommend_serve(
        session, _ns(calib_manifest=path, calib_mode="manifest-required")) == 0
    n1 = _count(session, run_id)
    assert n1 > 0
    assert _cmd_recommend_serve(
        session, _ns(calib_manifest=path, calib_mode="manifest-required")) == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert _count(session, run_id) == n1  # no append-only duplication


def test_backfill_manifest_fail_closed_before_the_loop(session, tmp_path, capsys):
    """FR-022: an invalid manifest aborts the whole backfill BEFORE per-race isolation (0 rows)."""
    from horseracing_betting.cli import _cmd_recommend_backfill
    _setup(session, tmp_path)
    bad = _write_manifest(tmp_path, fit_through="2007-12-31", scope="fixture", eligible=False)
    rc = _cmd_recommend_backfill(session, argparse.Namespace(
        from_=datetime.date(2008, 1, 1), to=datetime.date(2008, 12, 31),
        stage_discount=False, win_odds_cap=None,
        calib_manifest=bad, calib_mode="manifest-required", database_url=None))
    assert rc == 1
    assert "ERROR" in capsys.readouterr().out
    assert session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_backfill_manifest_generates_and_is_idempotent(session, tmp_path, capsys):
    """Backfill happy path: manifest-sourced recs, then a clean idempotent re-run."""
    from horseracing_betting.cli import _cmd_recommend_backfill
    _setup(session, tmp_path)
    path = _write_manifest(tmp_path, fit_through="2007-12-31")
    ns = lambda: argparse.Namespace(  # noqa: E731
        from_=datetime.date(2008, 1, 1), to=datetime.date(2008, 12, 31),
        stage_discount=False, win_odds_cap=None,
        calib_manifest=path, calib_mode="manifest-required", database_url=None)
    assert _cmd_recommend_backfill(session, ns()) == 0
    capsys.readouterr()
    n1 = session.scalar(select(func.count()).select_from(Recommendation))
    assert n1 > 0
    assert _cmd_recommend_backfill(session, ns()) == 0
    assert session.scalar(select(func.count()).select_from(Recommendation)) == n1


def test_cli_validation_relative_path_and_contradiction(session, tmp_path, capsys):
    """cli-contract: relative path / mode↔manifest contradiction are typed errors (exit 2)."""
    _setup(session, tmp_path)
    assert _cmd_recommend_serve(
        session, _ns(calib_manifest="rel/m.json", calib_mode="manifest-required")) == 2
    assert _cmd_recommend_serve(
        session, _ns(calib_manifest=None, calib_mode="manifest-required")) == 2
    assert _cmd_recommend_serve(
        session, _ns(calib_manifest="/abs/m.json", calib_mode="legacy-runtime")) == 2
