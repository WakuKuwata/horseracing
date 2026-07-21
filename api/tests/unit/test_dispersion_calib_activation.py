"""Feature 076 US3 (T022): dispersion model_delta from the immutable manifest, fail-OPEN.

Unlike betting/serving (fail-closed), the display instrument must never break the read API: a
missing / out-of-scope / in-fit-window / undated manifest just omits model_delta. When the manifest
IS applicable, its two-gamma drives model_delta and is generation-bound to the SELECTED run's model.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from horseracing_probability.calib_manifest import build_manifest

from horseracing_api.dispersion import load_activation_calibrator

_MODEL = "lgbm-063"
_AFTER = datetime.date(2025, 6, 1)   # after fit_through
_WITHIN = datetime.date(2024, 6, 1)  # within fit_through window


def _manifest(tmp_path: Path, *, fit_through="2024-12-31", scope="production", eligible=True,
              name="m.json") -> str:
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
    p = (tmp_path / name).resolve()
    p.write_text(json.dumps(m), encoding="utf-8")
    return str(p)


def test_applicable_manifest_yields_two_gamma(tmp_path):
    cal = load_activation_calibrator(
        active_model_version=_MODEL, target_date=_AFTER, manifest_path=_manifest(tmp_path))
    assert cal is not None
    assert cal.method == "two_gamma"
    assert cal.params["gamma_lo"] == 1.6 and cal.params["gamma_hi"] == 0.5


def test_no_manifest_configured_fails_open_to_none(tmp_path):
    assert load_activation_calibrator(active_model_version=_MODEL, target_date=_AFTER) is None


def test_missing_target_date_fails_open(tmp_path):
    assert load_activation_calibrator(
        active_model_version=_MODEL, target_date=None, manifest_path=_manifest(tmp_path)) is None


def test_race_within_fit_window_fails_open(tmp_path):
    # a historical race the manifest may NOT calibrate → omit model_delta (honest, not an error)
    assert load_activation_calibrator(
        active_model_version=_MODEL, target_date=_WITHIN, manifest_path=_manifest(tmp_path)) is None


def test_generation_mismatch_fails_open(tmp_path):
    # the selected run is a different model → the manifest does not apply → omit (fail-open)
    assert load_activation_calibrator(
        active_model_version="lgbm-999", target_date=_AFTER,
        manifest_path=_manifest(tmp_path)) is None


def test_fixture_scope_fails_open(tmp_path):
    assert load_activation_calibrator(
        active_model_version=_MODEL, target_date=_AFTER,
        manifest_path=_manifest(tmp_path, scope="fixture", eligible=False)) is None


def test_tampered_manifest_fails_open(tmp_path):
    path = _manifest(tmp_path)
    data = json.loads(Path(path).read_text())
    data["full_precision_params"]["two_gamma"]["gamma_lo"] = 9.9  # digest now stale
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    assert load_activation_calibrator(
        active_model_version=_MODEL, target_date=_AFTER, manifest_path=path) is None
