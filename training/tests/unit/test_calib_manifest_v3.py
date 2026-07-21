"""Feature 078 v3 (T009): manifest v3 — verifier-recomputed eligibility (D9) + verdict matrix (D6).

v3 carries a structured per-stage evaluation; verify_manifest RECOMPUTES activation_eligible and the
identity⟺verdict consistency, so a hand-crafted manifest cannot claim eligibility its verdicts do not
support. v2 (076 fixture-first) stays valid — both are accepted by the loader.
"""

from __future__ import annotations

import copy

import pytest
from horseracing_probability.calib_manifest import (
    ELIGIBILITY_POLICY_VERSION,
    ManifestError,
    build_manifest_v3,
    verify_manifest,
)

_DIG = "a" * 64
_EVAL = {"two_gamma": {"verdict": "ADOPT"}, "stage": {"verdict": "REJECT"}}


def _gamma(lo=1.6, hi=0.5, pivot=0.15):
    return {"gamma_lo": lo, "gamma_hi": hi, "pivot": pivot}


def _stage(l2=0.82, l3=0.70):
    return {"lambda2": l2, "lambda3": l3}


def _build(*, tg_verdict, tg_params, st_verdict, st_params,
           tg_ft="2024-12-31", st_ft="2024-12-31"):
    return build_manifest_v3(
        attestation_digest=_DIG, bundle_digest="b" * 64, evaluation=_EVAL,
        code_sha="078", seed=1, num_threads=1,
        two_gamma_verdict=tg_verdict, two_gamma_params=tg_params,
        two_gamma_fit_through=tg_ft, two_gamma_fit_race_set_hash="h1", two_gamma_n_fit=100,
        stage_verdict=st_verdict, stage_params=st_params,
        stage_fit_through=st_ft, stage_fit_race_set_hash="h2", stage_n_fit=90,
    )


# --- verdict matrix (D6): 6 rows -------------------------------------------------

def test_adopt_adopt_is_eligible_with_fitted_params():
    m = _build(tg_verdict="ADOPT", tg_params=_gamma(), st_verdict="ADOPT", st_params=_stage())
    assert m["schema_version"] == 3 and m["activation_eligible"] is True
    assert m["stages_evaluation"]["stage_discount_topk"]["consumer_pipeline"] == "serving_raw"
    verify_manifest(m)  # round-trips


def test_adopt_reject_is_eligible_stage_identity():
    m = _build(tg_verdict="ADOPT", tg_params=_gamma(),
               st_verdict="REJECT", st_params=_stage(1.0, 1.0), st_ft=None)
    assert m["activation_eligible"] is True
    assert m["stages_evaluation"]["stage_discount_topk"]["identity"] is True


def test_reject_reject_is_eligible_all_identity():
    m = _build(tg_verdict="REJECT", tg_params=_gamma(1.0, 1.0), tg_ft=None,
               st_verdict="REJECT", st_params=_stage(1.0, 1.0), st_ft=None)
    assert m["activation_eligible"] is True  # a validated decision NOT to calibrate (D6)
    assert m["fit_through"] == "1970-01-01"  # nothing fit → permissive floor (identity no-op)


def test_no_decision_blocks_eligibility():
    m = _build(tg_verdict="NO_DECISION", tg_params=_gamma(1.0, 1.0), tg_ft=None,
               st_verdict="ADOPT", st_params=_stage())
    assert m["activation_eligible"] is False


# --- D9: verifier recomputes — tampering is rejected ------------------------------

def test_tampered_eligibility_bool_is_rejected():
    m = _build(tg_verdict="NO_DECISION", tg_params=_gamma(1.0, 1.0), tg_ft=None,
               st_verdict="ADOPT", st_params=_stage())
    bad = copy.deepcopy(m)
    bad["activation_eligible"] = True  # claim eligibility a NO_DECISION does not support
    with pytest.raises(ManifestError, match="policy-recomputed"):
        verify_manifest(bad)


def test_nonidentity_params_without_adopt_is_rejected():
    m = _build(tg_verdict="ADOPT", tg_params=_gamma(), st_verdict="ADOPT", st_params=_stage())
    bad = copy.deepcopy(m)
    # flip the stage verdict to REJECT while leaving nonidentity λ + identity=False
    bad["stages_evaluation"]["stage_discount_topk"]["verdict"] = "REJECT"
    with pytest.raises(ManifestError, match="require ADOPT"):
        verify_manifest(bad)


def test_full_precision_divergence_is_rejected():
    m = _build(tg_verdict="ADOPT", tg_params=_gamma(), st_verdict="ADOPT", st_params=_stage())
    bad = copy.deepcopy(m)
    bad["full_precision_params"]["stage_lambdas"]["top2"] = 0.99  # diverge from stages_evaluation
    with pytest.raises(ManifestError, match="stage_lambdas"):
        verify_manifest(bad)


def test_unknown_eligibility_policy_rejected():
    m = _build(tg_verdict="ADOPT", tg_params=_gamma(), st_verdict="ADOPT", st_params=_stage())
    bad = copy.deepcopy(m)
    bad["eligibility_policy_version"] = "eligibility-v99"
    with pytest.raises(ManifestError, match="eligibility_policy_version"):
        verify_manifest(bad)


def test_policy_version_recorded():
    m = _build(tg_verdict="ADOPT", tg_params=_gamma(), st_verdict="ADOPT", st_params=_stage())
    assert m["eligibility_policy_version"] == ELIGIBILITY_POLICY_VERSION


def test_v3_manifest_activates_through_the_076_loader(tmp_path):
    """The crucial coexistence point: a REAL v3 manifest is loadable by the 076 activation loader
    (production scope + eligible + generation + temporal), and yields the shipped γ/λ."""
    import datetime
    import json

    from horseracing_probability.calib_activation import Profile, load_calibration

    m = _build(tg_verdict="ADOPT", tg_params=_gamma(1.6, 0.5), st_verdict="ADOPT",
               st_params=_stage(0.82, 0.70))
    path = (tmp_path / "v3.json").resolve()
    path.write_text(json.dumps(m), encoding="utf-8")

    act = load_calibration(
        str(path), active_model_version="lgbm-063",
        target_date=datetime.date(2025, 6, 1), profile=Profile.PRODUCTION,
    )
    assert act.two_gamma.params["gamma_lo"] == 1.6
    assert (act.stage_discount.lambda2, act.stage_discount.lambda3) == (0.82, 0.70)


def test_v3_non_eligible_is_rejected_by_production_loader(tmp_path):
    import datetime
    import json

    from horseracing_probability.calib_activation import ActivationError, Profile, load_calibration

    # a NO_DECISION stage → not eligible → the production loader refuses it (fail-closed)
    m = _build(tg_verdict="NO_DECISION", tg_params=_gamma(1.0, 1.0), tg_ft=None,
               st_verdict="ADOPT", st_params=_stage())
    path = (tmp_path / "v3ne.json").resolve()
    path.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ActivationError, match="not production-eligible"):
        load_calibration(str(path), active_model_version="lgbm-063",
                         target_date=datetime.date(2025, 6, 1), profile=Profile.PRODUCTION)
