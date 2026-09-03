"""Feature 108 (T010): tamper detection and generation-mismatch detection stay strict.

Feature 108 branches attestation construction and validation by calibration method. The risk of
any such branch is that it becomes an escape hatch: a payload that takes the new path could skip
checks the old path applied. These tests pin the two guarantees that must survive the branch —
the digest still covers every recorded field, and a model directory swapped underneath an
attestation is still caught (the `save_model_version` overwrite scenario 076 fail-closes on).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from horseracing_training.legacy_attest import (
    AttestationError,
    attestation_from_model_dir,
    recipe_from_attestation,
)

_REPO = Path(__file__).resolve().parents[3]
_LEGACY_DIR = _REPO / "artifacts" / "model_versions" / "lgbm-063"
_ARM_E_DIR = _REPO / "artifacts" / "model_versions" / "lgbm-094-cap900"
_CODE_SHA = "c8fc3a9cc7b55e6d0ce5da6598d609e4d3a80202"

_needs_legacy = pytest.mark.skipif(not _LEGACY_DIR.exists(), reason="lgbm-063 artifacts absent")
_needs_arm_e = pytest.mark.skipif(not _ARM_E_DIR.exists(), reason="arm E artifacts absent")


def _mutate(att: dict, key: str, value) -> dict:
    out = copy.deepcopy(att)
    out[key] = value
    return out


@_needs_legacy
def test_tampering_any_recorded_field_breaks_the_digest():
    att = attestation_from_model_dir(_LEGACY_DIR, code_sha=_CODE_SHA)
    # every payload field is covered by the digest — walk them all rather than spot-checking one,
    # so a future refactor that drops a field from the hashed payload fails here.
    seen_digest_rejection = False
    for key, value in att.items():
        if key == "attestation_digest":
            continue
        altered = "__tampered__" if isinstance(value, str) else json.loads(json.dumps(value))
        if altered == value:  # non-string: perturb structurally
            altered = [*value, "x"] if isinstance(value, list) else {**value, "__x__": 1} \
                if isinstance(value, dict) else 12345
        # Any rejection counts: some fields (e.g. base_model_version) are caught by the legacy
        # pin BEFORE the digest comparison. What must never happen is a tampered field being
        # accepted.
        with pytest.raises(AttestationError) as excinfo:
            recipe_from_attestation(_mutate(att, key, altered))
        if "digest mismatch" in str(excinfo.value):
            seen_digest_rejection = True
    assert seen_digest_rejection, "no field was caught by the digest comparison itself"


@_needs_legacy
def test_a_recomputed_digest_on_a_tampered_payload_is_not_accepted_either():
    """Re-signing is not a bypass: the payload must still satisfy the field validators."""
    from horseracing_eval.hashing import stable_hash

    att = attestation_from_model_dir(_LEGACY_DIR, code_sha=_CODE_SHA)
    bad = _mutate(att, "seed", "not-an-int")
    payload = {k: v for k, v in bad.items() if k != "attestation_digest"}
    bad["attestation_digest"] = stable_hash(payload)  # digest now "valid"
    with pytest.raises(AttestationError):
        recipe_from_attestation(bad)


@_needs_legacy
@_needs_arm_e
def test_model_dir_swap_is_detected_by_recomputation():
    """The 076 binding recomputes from the model dir; two generations must never agree."""
    legacy = attestation_from_model_dir(_LEGACY_DIR, code_sha=_CODE_SHA)
    arm_e = attestation_from_model_dir(_ARM_E_DIR, code_sha=_CODE_SHA)
    assert legacy["attestation_digest"] != arm_e["attestation_digest"]
    assert legacy["base_model_version"] != arm_e["base_model_version"]


@_needs_legacy
def test_code_sha_participates_in_the_digest():
    """A different code generation must not reuse another generation's digest."""
    a = attestation_from_model_dir(_LEGACY_DIR, code_sha=_CODE_SHA)
    b = attestation_from_model_dir(_LEGACY_DIR, code_sha="0" * 40)
    assert a["attestation_digest"] != b["attestation_digest"]
