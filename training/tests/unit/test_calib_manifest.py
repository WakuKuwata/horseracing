from __future__ import annotations

import copy
import json

import pytest

from horseracing_training.calib_manifest import (
    BASE_MODEL_VERSION,
    ManifestConflict,
    ManifestError,
    build_manifest,
    verify_manifest,
    write_manifest,
)

ATTESTATION_DIGEST = "a" * 64
BUNDLE_DIGEST = "b" * 64
GAMMA_LO = 0.9876543210123456


def _evaluation(*, method: str = "two_gamma") -> dict:
    return {
        "evaluation_contract_version": "v2",
        "base_model_version": BASE_MODEL_VERSION,
        "attestation_digest": ATTESTATION_DIGEST,
        "bundle_digest": BUNDLE_DIGEST,
        "stages": [{"stage": "two_gamma_win", "method": method}],
        "verdict": "NO_DECISION",
    }


def _manifest(*, evaluation: dict | None = None) -> dict:
    return build_manifest(
        attestation_digest=ATTESTATION_DIGEST,
        bundle_digest=BUNDLE_DIGEST,
        evaluation=evaluation or _evaluation(),
        stages=["model_win", "two_gamma_win", "stage_discount_top2"],
        code_sha="074deadbeef",
        seed=74,
        num_threads=1,
        two_gamma_lambda_full_precision={
            "two_gamma": {
                "gamma_lo": GAMMA_LO,
                "gamma_hi": 1.1234567890123457,
                "pivot": 0.15,
            },
            "stage_lambdas": {
                "top2": 0.9234567890123456,
                "top3": 0.8765432109876543,
            },
        },
    )


def test_full_precision_params_survive_manifest_round_trip(tmp_path):
    manifest = _manifest()
    path = write_manifest(tmp_path, manifest)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["full_precision_params"]["two_gamma"]["gamma_lo"] == GAMMA_LO
    assert loaded["full_precision_params"]["two_gamma"]["gamma_lo"] != round(GAMMA_LO, 5)
    verify_manifest(path)


def test_same_payload_has_same_digest_and_idempotent_write(tmp_path):
    first = _manifest()
    second = _manifest()

    assert first["manifest_digest"] == second["manifest_digest"]
    first_path = write_manifest(tmp_path, first)
    assert write_manifest(tmp_path, second) == first_path


def test_same_bundle_key_with_different_content_conflicts(tmp_path):
    manifest = _manifest()
    write_manifest(tmp_path, manifest)
    changed = copy.deepcopy(manifest)
    changed["seed"] += 1

    with pytest.raises(ManifestConflict):
        write_manifest(tmp_path, changed)


def test_tampered_checksum_is_rejected():
    manifest = _manifest()
    manifest["checksums"]["evaluation"] = "0" * 64

    with pytest.raises(ManifestError, match="checksum mismatch"):
        verify_manifest(manifest)


def test_unknown_schema_version_is_rejected():
    manifest = _manifest()
    manifest["schema_version"] = 999

    with pytest.raises(ManifestError, match="unknown schema_version"):
        verify_manifest(manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest.pop("evaluation"), "partial manifest"),
        (
            lambda manifest: manifest.__setitem__("base_model_version", "lgbm-999"),
            "generation mismatch",
        ),
    ],
)
def test_partial_or_generation_mismatch_is_rejected(mutation, message):
    manifest = _manifest()
    mutation(manifest)

    with pytest.raises(ManifestError, match=message):
        verify_manifest(manifest)


def test_identity_fallback_stage_is_explicitly_representable():
    manifest = _manifest(evaluation=_evaluation(method="identity"))

    assert manifest["evaluation"]["stages"] == [
        {"stage": "two_gamma_win", "method": "identity"}
    ]
    verify_manifest(manifest)
