"""Feature 082: general (non-legacy-pinned) attestation factory tests.

The legacy path stays pinned to lgbm-063/features-017; the general path validates the same
digest + payload but binds to a caller-stated (DB-resolved) model/feature version instead.
"""

from __future__ import annotations

import copy

import pytest

from horseracing_training.legacy_attest import (
    AttestationError,
    _validated_payload,
    general_factory_from_attestation,
)


def _payload_base() -> dict:
    """Minimal well-formed non-legacy payload (mirrors build_attestation output shape)."""
    return {
        "base_model_version": "lgbm-064-f02acc",
        "objective": "pl_topk",
        "postprocess": "group_softmax",
        "feature_version": "features-018",
        "ordered_feature_columns": ["venue_code", "distance", "age"],
        "resolved_lgbm_params": {"n_estimators": 3, "num_leaves": 4},
        "target_encode_cols": ["jockey_id", "trainer_id"],
        "te_smoothing": 10.0,
        "internal_calibration": {
            "method": "isotonic", "calib_frac": 0.3,
            "calibration_split_unit": "race_count_v1",
        },
        "seed": 42,
        "num_threads": 1,
        "drop_features": [],
        "source_fingerprint": None,
        "materialized_hash": None,
        "code_sha": "deadbeef",
    }


def _att(payload: dict) -> dict:
    from horseracing_eval.hashing import stable_hash
    return {**payload, "attestation_digest": stable_hash(payload)}


def test_general_validation_accepts_non_legacy_model():
    payload = _validated_payload(_att(_payload_base()), enforce_legacy=False)
    assert payload["base_model_version"] == "lgbm-064-f02acc"


def test_legacy_validation_still_rejects_non_legacy_model():
    with pytest.raises(AttestationError, match="legacy base_model_version"):
        _validated_payload(_att(_payload_base()), enforce_legacy=True)


def test_general_factory_rejects_model_version_mismatch():
    with pytest.raises(AttestationError, match="differs from the DB-resolved active"):
        general_factory_from_attestation(
            None, _att(_payload_base()),
            expected_model_version="lgbm-065", expected_feature_version="features-018",
        )


def test_general_factory_rejects_feature_version_mismatch():
    with pytest.raises(AttestationError, match="feature_version differs"):
        general_factory_from_attestation(
            None, _att(_payload_base()),
            expected_model_version="lgbm-064-f02acc", expected_feature_version="features-017",
        )


def test_general_factory_rejects_tampered_digest():
    att = _att(_payload_base())
    tampered = copy.deepcopy(att)
    tampered["seed"] = 43  # payload changed, digest stale
    with pytest.raises(AttestationError, match="digest mismatch"):
        general_factory_from_attestation(
            None, tampered,
            expected_model_version="lgbm-064-f02acc", expected_feature_version="features-018",
        )


def test_general_factory_builds_recipe_faithfully():
    f = general_factory_from_attestation(
        None, _att(_payload_base()),
        expected_model_version="lgbm-064-f02acc", expected_feature_version="features-018",
    )
    assert f.recipe.objective == "pl_topk"
    assert f.recipe.calibration == "isotonic"
    assert f.recipe.calib_frac == 0.3
    assert f.ordered_feature_columns == ("venue_code", "distance", "age")
    assert f.num_threads == 1
