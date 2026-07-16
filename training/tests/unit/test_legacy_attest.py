"""Feature 074 US2: complete, content-addressed lgbm-063 recipe attestation."""

from __future__ import annotations

import json

import pytest

from horseracing_training.legacy_attest import (
    AttestationError,
    attestation_from_model_dir,
    build_attestation,
    factory_from_attestation,
    recipe_from_attestation,
)
from horseracing_training.recipe import ModelRecipe, RecipeFactory

REQUIRED_FIELDS = {
    "base_model_version",
    "resolved_lgbm_params",
    "objective",
    "postprocess",
    "ordered_feature_columns",
    "feature_version",
    "target_encode_cols",
    "te_smoothing",
    "internal_calibration",
    "seed",
    "num_threads",
    "drop_features",
    "source_fingerprint",
    "materialized_hash",
    "code_sha",
    "attestation_digest",
}


def _metadata() -> dict:
    return {
        "model_version": "lgbm-063",
        "params": {
            "objective": "binary",
            "n_estimators": 300,
            "learning_rate": 0.05,
            "num_leaves": 31,
        },
        "objective": "pl_topk",
        "postprocess": "group_softmax",
        "feature_columns": ["venue_code", "distance", "jockey_id", "trainer_id"],
        "feature_version": "features-017",
        "target_encode_cols": ["jockey_id", "trainer_id"],
        "te_smoothing": 10.0,
        "seed": 42,
        "calibration": "isotonic",
        "calib_frac": 0.3,
    }


def _attestation(tmp_path) -> dict:
    return build_attestation(tmp_path, _metadata(), code_sha="abc123")


def test_build_attestation_contains_complete_resolved_recipe(tmp_path):
    att = _attestation(tmp_path)

    assert set(att) == REQUIRED_FIELDS
    assert att["base_model_version"] == "lgbm-063"
    assert att["resolved_lgbm_params"] == _metadata()["params"]
    assert att["ordered_feature_columns"] == _metadata()["feature_columns"]
    assert att["internal_calibration"] == {
        "method": "isotonic",
        "calib_frac": 0.3,
        "calibration_split_unit": "race_count_v1",
    }
    assert att["num_threads"] == 1
    assert att["drop_features"] == []
    assert att["source_fingerprint"] is None
    assert att["materialized_hash"] is None


def test_digest_is_deterministic_and_mapping_order_independent(tmp_path):
    metadata = _metadata()
    reordered = dict(reversed(list(metadata.items())))
    reordered["params"] = dict(reversed(list(metadata["params"].items())))

    first = build_attestation(tmp_path, metadata, code_sha="abc123")
    second = build_attestation(tmp_path, reordered, code_sha="abc123")

    assert first == second
    assert first["attestation_digest"] == second["attestation_digest"]


def test_recipe_from_attestation_rejects_missing_required_field(tmp_path):
    att = _attestation(tmp_path)
    del att["ordered_feature_columns"]

    with pytest.raises(AttestationError, match="missing required fields"):
        recipe_from_attestation(att)


def test_recipe_from_attestation_returns_stable_model_recipe(tmp_path):
    att = _attestation(tmp_path)

    first = recipe_from_attestation(att)
    second = recipe_from_attestation(dict(reversed(list(att.items()))))

    assert isinstance(first, ModelRecipe)
    assert first == second
    assert first.recipe_hash() == second.recipe_hash()
    assert first.objective == "pl_topk"
    assert first.calibration == "isotonic"
    assert first.calib_frac == 0.3
    assert first.calibration_split_unit == "race_count_v1"
    assert first.target_encode_cols == ("jockey_id", "trainer_id")


def test_differing_field_changes_attestation_digest(tmp_path):
    metadata = _metadata()
    first = build_attestation(tmp_path, metadata, code_sha="abc123")
    changed = build_attestation(tmp_path, {**metadata, "seed": 7}, code_sha="abc123")

    assert first["attestation_digest"] != changed["attestation_digest"]


def test_attestation_from_model_dir_reads_metadata_and_freeze(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps(_metadata()))
    (tmp_path / "freeze_073.json").write_text(
        json.dumps(
            {
                "model_version": "lgbm-063",
                "calibration_split_unit": "race_count_v1",
            }
        )
    )

    att = attestation_from_model_dir(tmp_path, code_sha="abc123")

    assert att["internal_calibration"]["calibration_split_unit"] == "race_count_v1"


def test_factory_builder_retains_resolved_params_and_feature_order(tmp_path):
    att = _attestation(tmp_path)

    factory = factory_from_attestation(object(), att)

    assert isinstance(factory, RecipeFactory)
    assert factory.resolved_lgbm_params == att["resolved_lgbm_params"]
    assert factory.ordered_feature_columns == tuple(att["ordered_feature_columns"])
    assert factory.recipe.recipe_hash() == recipe_from_attestation(att).recipe_hash()
