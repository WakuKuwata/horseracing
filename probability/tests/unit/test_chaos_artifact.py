"""Feature 084: fail-closed chaos artifact loader and temporal boundary tests."""

from __future__ import annotations

import datetime
import hashlib
import json

import pytest

from horseracing_probability.chaos_artifact import (
    ChaosArtifactApprovalError,
    ChaosArtifactDigestError,
    ChaosArtifactEdgesError,
    ChaosArtifactOperationalError,
    ChaosArtifactOutOfValidityWindowError,
    ChaosArtifactParseError,
    ChaosArtifactSchemaError,
    ChaosArtifactWindowError,
    ChaosBandsArtifact,
    compute_chaos_artifact_digest,
    load_chaos_artifact,
)

_TARGET_2026 = datetime.date(2026, 7, 26)


def _independent_digest(payload: dict) -> str:
    covered = {key: value for key, value in payload.items() if key != "artifact_digest"}
    canonical = json.dumps(
        covered,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload() -> dict:
    payload = {
        "version": "chaosbands-v1",
        "label_definition": "top3_popularity_composition_proxy_v1",
        "lambda2": 0.8312,
        "lambda3": 0.7101,
        "lambda_fit_objective": {
            "lambda2": "conditional_nll_stage2",
            "lambda3": "conditional_nll_stage3",
        },
        "band_axis": "p_s_ge_20",
        "quintile_edges": [0.0196, 0.0659, 0.1117, 0.1702],
        "edges_basis": "closing_history",
        "s_threshold_basis": "fit_window_p90",
        "fit_from": "2020-01-01",
        "fit_to": "2023-12-31",
        "as_of": "2023-12-31",
        "fit_through": "2023-12-31",
        "valid_from": "2024-01-01",
        "n_races_fit": 13_747,
        "race_set_hash": "a" * 64,
        "fit_input_hash": "b" * 64,
        "preregistration": {
            "events": ["s_ge_20", "himo_are", "total_collapse", "s_ge_30"],
            "min_positives": 100,
            "min_race_days": 60,
            "primary_horizon": {
                "minimum_seconds_to_post": 600,
                "maximum_seconds_to_post": 86_400,
            },
        },
        "numeric_stability_report": {
            "status": "green",
            "representative_fields_passed": True,
            "adversarial_fields_passed": True,
        },
        "operational_lambda_envelope": {
            "lambda2": {"min_exclusive": 0.0, "max_inclusive": 1.0},
            "lambda3": {"min_exclusive": 0.0, "max_inclusive": 1.0},
        },
        "eligibility_predicate": {
            "complete_odds": True,
            "unique_popularity": True,
            "min_field_size": 4,
        },
        "field_size_reference_quantiles": {
            "18": [0.05, 0.10, 0.15, 0.20],
        },
        "code_sha": "c" * 40,
        "calibration_status": "provisional",
    }
    payload["artifact_digest"] = _independent_digest(payload)
    return payload


def _write(tmp_path, payload: dict, *, name: str = "artifact.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _load(tmp_path, payload: dict, *, target_date: datetime.date = _TARGET_2026):
    path = _write(tmp_path, payload)
    return load_chaos_artifact(
        path,
        approved_digests={payload["artifact_digest"]},
        target_date=target_date,
    )


def _redigest(payload: dict) -> None:
    payload["artifact_digest"] = _independent_digest(payload)


def test_valid_artifact_loads_and_2026_race_is_accepted(tmp_path):
    payload = _payload()
    artifact = _load(tmp_path, payload)

    assert isinstance(artifact, ChaosBandsArtifact)
    assert artifact.lambda2 == pytest.approx(0.8312)
    assert artifact.lambda3 == pytest.approx(0.7101)
    assert artifact.fit_through == datetime.date(2023, 12, 31)
    assert artifact.valid_from == datetime.date(2024, 1, 1)
    assert artifact.artifact_digest == payload["artifact_digest"]


# --- the normative eight checks, in order -----------------------------------


def test_step1_invalid_json_has_typed_reason(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ChaosArtifactParseError) as raised:
        load_chaos_artifact(path, approved_digests=set(), target_date=_TARGET_2026)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "json_parse"


def test_step2_missing_required_key_has_typed_reason(tmp_path):
    payload = _payload()
    del payload["band_axis"]

    with pytest.raises(ChaosArtifactSchemaError) as raised:
        _load(tmp_path, payload)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "missing_required_keys"


def test_step3_digest_mismatch_has_typed_reason(tmp_path):
    payload = _payload()
    payload["lambda2"] = 0.9

    with pytest.raises(ChaosArtifactDigestError) as raised:
        _load(tmp_path, payload)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "digest_mismatch"


def test_step4_unapproved_digest_has_typed_reason(tmp_path):
    payload = _payload()
    path = _write(tmp_path, payload)

    with pytest.raises(ChaosArtifactApprovalError) as raised:
        load_chaos_artifact(path, approved_digests={"d" * 64}, target_date=_TARGET_2026)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "digest_not_approved"


@pytest.mark.parametrize(
    "edges",
    (
        [0.01, 0.02, 0.03],
        [0.01, 0.02, 0.02, 0.04],
        [0.01, 0.03, 0.02, 0.04],
    ),
)
def test_step5_edges_must_be_four_and_strictly_increasing(tmp_path, edges):
    payload = _payload()
    payload["quintile_edges"] = edges
    _redigest(payload)

    with pytest.raises(ChaosArtifactEdgesError) as raised:
        _load(tmp_path, payload)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "invalid_quintile_edges"


def test_step6_lambda_must_be_inside_artifact_envelope(tmp_path):
    payload = _payload()
    payload["lambda2"] = 1.1
    _redigest(payload)

    with pytest.raises(ChaosArtifactOperationalError) as raised:
        _load(tmp_path, payload)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "operational_gate_failed"


def test_step6_numeric_stability_report_must_be_green(tmp_path):
    payload = _payload()
    payload["numeric_stability_report"]["status"] = "red"
    _redigest(payload)

    with pytest.raises(ChaosArtifactOperationalError, match="not green"):
        _load(tmp_path, payload)


def test_step7_valid_from_must_be_after_fit_through(tmp_path):
    payload = _payload()
    payload["valid_from"] = payload["fit_through"]
    _redigest(payload)

    with pytest.raises(ChaosArtifactWindowError) as raised:
        _load(tmp_path, payload)
    assert raised.value.reason == "artifact_unavailable"
    assert raised.value.failure == "invalid_artifact_window"


def test_step8_fit_to_date_is_rejected(tmp_path):
    payload = _payload()

    with pytest.raises(ChaosArtifactOutOfValidityWindowError) as raised:
        _load(tmp_path, payload, target_date=datetime.date(2023, 12, 31))
    assert raised.value.reason == "out_of_validity_window"
    assert raised.value.failure == "out_of_validity_window"


def test_step8_valid_from_minus_one_is_rejected(tmp_path):
    payload = _payload()
    # Leave a discovery-only gap after fit_through so this pins the second
    # conjunct independently of the fit-window boundary.
    payload["valid_from"] = "2024-02-01"
    _redigest(payload)

    with pytest.raises(ChaosArtifactOutOfValidityWindowError):
        _load(tmp_path, payload, target_date=datetime.date(2024, 1, 31))


def test_step8_valid_from_boundary_is_accepted(tmp_path):
    payload = _payload()
    artifact = _load(tmp_path, payload, target_date=datetime.date(2024, 1, 1))
    assert artifact.valid_from == datetime.date(2024, 1, 1)


# --- canonical digest coverage ----------------------------------------------


def test_digest_excludes_only_its_own_self_reference(tmp_path):
    payload = _payload()
    first = compute_chaos_artifact_digest(payload)
    payload["artifact_digest"] = "f" * 64
    second = compute_chaos_artifact_digest(payload)

    assert first == second == _independent_digest(payload)


def test_tampering_any_payload_field_is_caught_before_use(tmp_path):
    payload = _payload()
    approved_digest = payload["artifact_digest"]
    payload["preregistration"]["min_positives"] = 1
    path = _write(tmp_path, payload)

    with pytest.raises(ChaosArtifactDigestError):
        load_chaos_artifact(
            path,
            approved_digests={approved_digest},
            target_date=_TARGET_2026,
        )
