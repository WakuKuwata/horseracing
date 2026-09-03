"""Feature 108 (T008): the legacy attestation payload and digest must not move.

This single test is what protects the already-published production calibration manifest. That
manifest records `attestation_digest=ef9c5441…`, and the 076 activation loader re-derives the
attestation from the model directory and compares. If feature 108's arm E branch perturbs the
legacy payload by even one key, the digest changes, the comparison fails, and the frozen
manifest becomes unreferenceable — taking the recorded 078 verdict with it.

The golden was captured BEFORE any implementation work (evidence/legacy-attestation-golden.json)
and its digest was verified to equal the one inside the published manifest, so "the golden still
matches" and "activation still resolves" are the same statement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from horseracing_training.legacy_attest import attestation_from_model_dir

_REPO = Path(__file__).resolve().parents[3]
_GOLDEN = _REPO / "specs" / "108-arm-e-attestation" / "evidence" / "legacy-attestation-golden.json"
_MODEL_DIR = _REPO / "artifacts" / "model_versions" / "lgbm-063"
#: the code SHA recorded by the published production manifest (not an arbitrary constant —
#: using any other value would prove payload stability but not activation stability).
_CODE_SHA = "c8fc3a9cc7b55e6d0ce5da6598d609e4d3a80202"
_PUBLISHED_MANIFEST_DIGEST = "ef9c5441ea385cfdf1eca550c8a7ec1f002de48367701aa80a6bee968903e8d4"


def _golden() -> dict:
    if not _GOLDEN.exists():  # pragma: no cover - artifact is committed
        pytest.skip(f"golden not captured: {_GOLDEN}")
    return json.loads(_GOLDEN.read_text())["attestation"]


@pytest.mark.skipif(not _MODEL_DIR.exists(), reason="lgbm-063 artifacts not present")
def test_legacy_attestation_payload_is_byte_identical_to_the_golden():
    got = attestation_from_model_dir(_MODEL_DIR, code_sha=_CODE_SHA)
    want = _golden()
    # compare the payload key-by-key first so a failure names the offending field instead of
    # just reporting "two long dicts differ"
    assert set(got) == set(want), "attestation gained or lost a top-level key"
    for key in sorted(want):
        assert got[key] == want[key], f"legacy attestation field changed: {key}"


@pytest.mark.skipif(not _MODEL_DIR.exists(), reason="lgbm-063 artifacts not present")
def test_legacy_digest_still_matches_the_published_manifest():
    got = attestation_from_model_dir(_MODEL_DIR, code_sha=_CODE_SHA)
    assert got["attestation_digest"] == _PUBLISHED_MANIFEST_DIGEST
    assert _golden()["attestation_digest"] == _PUBLISHED_MANIFEST_DIGEST
