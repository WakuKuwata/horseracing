"""Unit tests for the content-addressed OOF prediction bundle."""

from __future__ import annotations

from copy import deepcopy

import pytest
from horseracing_eval.hashing import race_set_hash, stable_hash

from horseracing_probability.oof_bundle import (
    SCHEMA_VERSION,
    BundleError,
    compute_bundle_digest,
    read_bundle,
    verify_bundle,
    write_bundle,
)


def _payload() -> dict:
    predictions = {
        "202301010101": {
            "horse-b": {"win": 0.25, "top2": 0.55, "top3": 0.75},
            "horse-a": {"win": 0.75, "top2": 0.9, "top3": 0.98},
        },
        "202401010101": {
            "horse-d": {"win": 0.4, "top2": 0.7, "top3": 0.9},
            "horse-c": {"win": 0.6, "top2": 0.85, "top3": 0.97},
        },
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "predictions": predictions,
        "fold_boundaries": [2023, 2024],
        "per_fold": [
            {
                "valid_year": 2023,
                "train_race_set_hash": "train-2023",
                "valid_race_set_hash": "valid-2023",
                "train_through": "2022-12-31",
                "model_digest": "model-2023",
            },
            {
                "valid_year": 2024,
                "train_race_set_hash": "train-2024",
                "valid_race_set_hash": "valid-2024",
                "train_through": "2023-12-31",
                "model_digest": "model-2024",
            },
        ],
        "oof_race_set_hash": race_set_hash(predictions),
        "prediction_checksum": stable_hash(predictions),
        "attestation_digest": "a" * 64,
    }
    payload["bundle_digest"] = compute_bundle_digest(payload)
    return payload


def test_digest_is_deterministic_and_order_independent_for_dicts_and_sets():
    left = _payload()
    right = deepcopy(left)

    right["predictions"] = {
        race_id: {
            horse_id: dict(reversed(tuple(probabilities.items())))
            for horse_id, probabilities in reversed(tuple(horses.items()))
        }
        for race_id, horses in reversed(tuple(left["predictions"].items()))
    }
    right["per_fold"] = [dict(reversed(tuple(fold.items()))) for fold in left["per_fold"]]
    left["per_fold"][0]["member_set"] = {"race-b", "race-a"}
    right["per_fold"][0]["member_set"] = set(reversed(("race-b", "race-a")))

    assert compute_bundle_digest(left) == compute_bundle_digest(left)
    assert compute_bundle_digest(left) == compute_bundle_digest(right)


def test_write_is_idempotent(tmp_path):
    payload = _payload()
    expected_digest = payload.pop("bundle_digest")

    first = write_bundle(tmp_path, payload)
    first_bytes = first.read_bytes()
    second = write_bundle(tmp_path, payload)

    assert first == second
    assert second == tmp_path / "artifacts" / "oof" / expected_digest / "bundle.json"
    assert second.read_bytes() == first_bytes
    assert not tuple((tmp_path / "artifacts" / "oof").glob("*.tmp"))


def test_verify_rejects_tampering_and_schema_mismatch():
    tampered = _payload()
    tampered["predictions"]["202301010101"]["horse-a"]["win"] = 0.74

    with pytest.raises(BundleError, match="prediction_checksum"):
        verify_bundle(tampered)

    wrong_schema = _payload()
    wrong_schema["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(BundleError, match="schema_version"):
        verify_bundle(wrong_schema)


def test_read_after_write_round_trips_equal_payload(tmp_path):
    payload = _payload()

    assert read_bundle(write_bundle(tmp_path, payload)) == payload


def test_digest_excludes_incidental_fields():
    first = _payload()
    second = deepcopy(first)
    first["generated_at"] = "2026-07-16T09:00:00+09:00"
    second["generated_at"] = "2026-07-16T10:00:00+09:00"

    assert compute_bundle_digest(first) == compute_bundle_digest(second)
