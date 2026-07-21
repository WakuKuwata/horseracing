"""Feature 076 T006/T007: load_calibration fail-closed + mapping + leak-guard (unit)."""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.calib_activation import (
    Activation,
    ActivationError,
    ActivationMode,
    Profile,
    load_calibration,
)
from horseracing_probability.calib_manifest import ManifestError
from tests.support import fixture_manifest as fm

_ACTIVE = "lgbm-063"
_TARGET = fm.DEFAULT_TARGET_DATE


# --- happy path + mapping ---------------------------------------------------

def test_production_eligible_manifest_activates(tmp_path):
    path = fm.production_fixture(tmp_path)
    act = load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)
    assert isinstance(act, Activation)
    assert act.mode == ActivationMode.MANIFEST_REQUIRED.value
    assert act.two_gamma.method == "two_gamma"
    # stage_lambdas{top2,top3} -> StageDiscount(lambda2, lambda3) — key mapping is load-bearing.
    assert act.stage_discount.lambda2 == pytest.approx(0.8234567890123456)
    assert act.stage_discount.lambda3 == pytest.approx(0.7098765432109876)
    assert ";calib=" in act.two_gamma.logic_version


def test_manifest_gamma_is_applied_at_full_precision(tmp_path):
    """SC-002 proof: the manifest γ drives the actual PROBABILITIES, not just the logic_version.

    A loader that parsed γ but never applied it (or rounded it to the %.5f audit string) would pass a
    string-only assertion. Here the calibrated vector is compared against the two-gamma transform
    computed from the manifest's FULL-precision γ, and a different γ must give a different vector.
    """
    from horseracing_probability.model_calibration import apply_p_calibrator

    lo, hi, pivot = 1.6543210987654321, 0.5123456789012345, 0.15
    act = load_calibration(
        fm.production_fixture(tmp_path, gamma_lo=lo, gamma_hi=hi, pivot=pivot),
        active_model_version=_ACTIVE, target_date=_TARGET,
    )
    p = {"a": 0.05, "b": 0.10, "c": 0.25, "d": 0.60}  # spans both sides of the pivot
    got = apply_p_calibrator(p, act.two_gamma)

    # hand-computed two-gamma: w(p)=p^lo (p<=pivot) else pivot^(lo-hi)*p^hi, then renormalise
    def w(x):
        return x ** lo if x <= pivot else (pivot ** (lo - hi)) * (x ** hi)
    raw = {k: w(v) for k, v in p.items()}
    total = sum(raw.values())
    expected = {k: v / total for k, v in raw.items()}
    for k in p:
        assert got[k] == pytest.approx(expected[k], rel=1e-12), f"{k}: full-precision γ not applied"

    # a different γ must move the numbers (guards against an ignored / hard-coded calibrator)
    act2 = load_calibration(
        fm.production_fixture(tmp_path, gamma_lo=2.5, gamma_hi=0.4, name="other.json"),
        active_model_version=_ACTIVE, target_date=_TARGET,
    )
    got2 = apply_p_calibrator(p, act2.two_gamma)
    assert any(got[k] != got2[k] for k in p)


def test_stage_lambda_key_mapping_is_exact(tmp_path):
    path = fm.production_fixture(tmp_path, top2=0.4, top3=0.9)
    act = load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)
    assert (act.stage_discount.lambda2, act.stage_discount.lambda3) == (0.4, 0.9)


# --- fixture profile (positive) + rejection (FR-016 / SC-010) ----------------

def test_fixture_profile_accepts_fixture_scope(tmp_path):
    path = fm.fixture_scoped(tmp_path)
    act = load_calibration(
        path, active_model_version=_ACTIVE, target_date=_TARGET, profile=Profile.FIXTURE
    )
    assert isinstance(act, Activation)


def test_production_profile_rejects_fixture_scope(tmp_path):
    path = fm.fixture_scoped(tmp_path)
    with pytest.raises(ActivationError, match="not production-eligible"):
        load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)


def test_production_profile_rejects_non_eligible(tmp_path):
    path = fm.production_fixture(tmp_path, activation_eligible=False)
    with pytest.raises(ActivationError, match="not production-eligible"):
        load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)


# --- generation binding (FR-019) --------------------------------------------

def test_base_model_mismatch_rejected(tmp_path):
    path = fm.production_fixture(tmp_path)
    with pytest.raises(ActivationError, match="generation mismatch"):
        load_calibration(path, active_model_version="lgbm-999", target_date=_TARGET)


def test_injected_attestation_verifier_runs_and_can_reject(tmp_path):
    path = fm.production_fixture(tmp_path)

    def bad_verifier(manifest):
        raise ActivationError("attestation mismatch (save_model_version overwrite?)")

    with pytest.raises(ActivationError, match="attestation mismatch"):
        load_calibration(
            path, active_model_version=_ACTIVE, target_date=_TARGET,
            attestation_verifier=bad_verifier,
        )


def test_injected_verifier_pass_activates(tmp_path):
    path = fm.production_fixture(tmp_path)
    seen = {}
    act = load_calibration(
        path, active_model_version=_ACTIVE, target_date=_TARGET,
        attestation_verifier=lambda m: seen.setdefault("digest", m["attestation_digest"]),
    )
    assert isinstance(act, Activation)
    assert seen["digest"] == "a" * 64


# --- structural fail-closed (FR-005 / SC-005) -------------------------------

def test_missing_file_rejected(tmp_path):
    with pytest.raises(ManifestError):
        load_calibration(
            tmp_path / "nope.json", active_model_version=_ACTIVE, target_date=_TARGET
        )


def test_relative_path_rejected():
    with pytest.raises(ActivationError, match="absolute"):
        load_calibration(
            "relative/manifest.json", active_model_version=_ACTIVE, target_date=_TARGET
        )


def test_tampered_manifest_rejected(tmp_path):
    path = fm.tampered(tmp_path)
    with pytest.raises(ManifestError):
        load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)


def test_unknown_schema_version_rejected(tmp_path):
    manifest = fm.make_manifest()
    manifest["schema_version"] = 1  # a pre-076 (v1) manifest must never activate
    path = fm.write_manifest_file(tmp_path, manifest)
    with pytest.raises(ManifestError, match="unknown schema_version"):
        load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)


def test_missing_stage_lambda_key_is_manifest_error(tmp_path):
    # A2/A1: a stage_lambdas without top3 must be a typed ManifestError, not a raw KeyError.
    manifest = fm.make_manifest()
    del manifest["full_precision_params"]["stage_lambdas"]["top3"]
    path = fm.write_manifest_file(tmp_path, manifest)
    with pytest.raises(ManifestError):
        load_calibration(path, active_model_version=_ACTIVE, target_date=_TARGET)


# --- temporal validity (FR-021 / SC-012) ------------------------------------

def test_target_within_fit_window_rejected(tmp_path):
    path = fm.production_fixture(tmp_path, fit_through="2025-12-31")
    with pytest.raises(ActivationError, match="fit window"):
        load_calibration(
            path, active_model_version=_ACTIVE, target_date=datetime.date(2025, 6, 1)
        )


def test_assert_applies_per_day(tmp_path):
    path = fm.production_fixture(tmp_path, fit_through="2025-01-31")
    act = load_calibration(path, active_model_version=_ACTIVE)  # no target_date -> load-once
    act.assert_applies(datetime.date(2025, 2, 1))  # after fit_through -> ok
    with pytest.raises(ActivationError):
        act.assert_applies(datetime.date(2025, 1, 15))  # within window -> reject


# --- leak-guard (FR-012 / SC-009) -------------------------------------------

def test_loader_does_not_touch_runtime_fit_or_results(tmp_path):
    """The activation path must never call the leaky loaders / any fit / query RaceResult."""
    import horseracing_probability.calib_activation as mod

    src = __import__("inspect").getsource(mod)
    for forbidden in ("load_p_samples", "_latest_run_predictions", "fit_p_calibrator",
                      "RaceResult", "fit_product_stage_discount"):
        assert forbidden not in src, f"activation loader must not reference {forbidden}"
