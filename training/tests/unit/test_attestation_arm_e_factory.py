"""Feature 108 (T011/T011b): the rebuilt procedure matches what was attested.

The claim this feature can honestly make is BEHAVIOURAL equality, not identity of a recipe
hash: the hash the model was registered with was never persisted (not in metadata.json, not in
model_versions), so there is nothing to compare against. What must hold is that every knob that
changes what the procedure DOES is carried over from the attestation rather than falling back to
a code default — the two that bite here are the OOF block count (default 3, real model 8) and
the booster capacity (default 300, real model 900).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.legacy_attest import (
    attestation_from_model_dir,
    attested_construction,
    general_factory_from_attestation,
)

_REPO = Path(__file__).resolve().parents[3]
_ARM_E_DIR = _REPO / "artifacts" / "model_versions" / "lgbm-094-cap900"
_MODEL_VERSION = "lgbm-094-cap900"
_FEATURE_VERSION = "features-021"
_needs_arm_e = pytest.mark.skipif(not _ARM_E_DIR.exists(), reason="arm E artifacts absent")


def _att() -> dict:
    return attestation_from_model_dir(_ARM_E_DIR, code_sha="t")


def _factory():
    return general_factory_from_attestation(
        None, _att(),
        expected_model_version=_MODEL_VERSION, expected_feature_version=_FEATURE_VERSION,
    )


@_needs_arm_e
def test_arm_e_rebuilds_an_oof_calibrated_factory():
    f = _factory()
    assert isinstance(f, CalibSplitFactory)
    assert f.method == "isotonic"
    # not the code default of 3 — the recorded value
    assert f.n_oof_blocks == 8
    # an insufficient OOF sample must abort rather than ship an identity calibrator under the
    # protocol's name (same fail-closed arm E's own registration uses)
    assert f.require_sufficient is True


@_needs_arm_e
def test_behavioural_configuration_matches_the_attestation():
    att = _att()
    want = attested_construction({k: v for k, v in att.items() if k != "attestation_digest"})
    f = _factory()

    assert f.recipe.objective == want["objective"]
    assert f.recipe.seed == want["seed"]
    assert f.recipe.te_smoothing == want["te_smoothing"]
    assert tuple(f.recipe.target_encode_cols) == want["target_encode_cols"]
    assert tuple(f.recipe.drop_features) == want["drop_features"]
    assert f.n_oof_blocks == want["n_oof_blocks"]
    # capacity survives the resolved-params -> recipe-override conversion
    assert dict(f.recipe.params or ())["n_estimators"] == want["resolved_lgbm_params"]["n_estimators"]


@_needs_arm_e
def test_declared_calib_frac_is_excluded_from_the_behavioural_comparison():
    """It is inert under this protocol version (`_make_base` hardcodes 0.0), and the registered
    hash it would have moved was never stored — claiming to match it would be a claim this
    feature cannot support."""
    att = _att()
    want = attested_construction({k: v for k, v in att.items() if k != "attestation_digest"})
    assert "calib_frac" not in want
    assert "calib_frac_requested" not in want


@_needs_arm_e
def test_weight_mask_reaches_the_fit_not_just_the_record():
    """T011b / FR-006: 091 established the mask is the mechanism, not a garnish.

    An OOF regeneration that recorded the mask but did not APPLY it would produce predictions the
    attested procedure never made, and every downstream calibration parameter would be fitted on
    them. Assert the spec the factory actually hands to the features layer.
    """
    f = _factory()
    spec = f.recipe.weight_mask_spec()
    assert spec is not None, "the attested mask must survive into the features-layer spec"
    assert (spec.rate, spec.seed) == (0.5, 20260810)
    assert spec.unit == "race"


@_needs_arm_e
def test_a_model_without_a_mask_declares_none_rather_than_a_silent_default():
    """Absence must be representable: no mask key => no mask, not a zero-rate mask."""
    att = _att()
    att.pop("weight_mask")
    # recompute the digest so the payload is self-consistent for this shape check
    from horseracing_eval.hashing import stable_hash

    payload = {k: v for k, v in att.items() if k != "attestation_digest"}
    att["attestation_digest"] = stable_hash(payload)
    f = general_factory_from_attestation(
        None, att, expected_model_version=_MODEL_VERSION,
        expected_feature_version=_FEATURE_VERSION,
    )
    assert f.recipe.weight_mask_rate is None
    assert f.recipe.weight_mask_spec() is None


@_needs_arm_e
def test_num_threads_disagreement_is_refused_not_ignored():
    """T011c: arm E used to accept `num_threads` and drop it.

    No path here propagates the value into LightGBM — the legacy factory uses it purely as an
    agreement check, so determinism does NOT come from it (contracts/attestation.md says so).
    What the attestation guarantees is that nobody ran the recipe under a thread count that
    contradicts the record; silently ignoring the argument removed even that.
    """
    from horseracing_training.legacy_attest import AttestationError

    f = _factory()
    assert f.attested_num_threads == 1
    with pytest.raises(AttestationError, match="differs from attested"):
        f.fit([], num_threads=99)
