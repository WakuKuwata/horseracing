"""T023: hash-contract scoping (FR-018, codex C5/analyze I3).

schema/content hashes are identical across arms sharing a feature_version; recipe/model hashes
are arm-specific but reproducible within-arm.
"""

from __future__ import annotations

from horseracing_eval.hashing import HashContract, race_set_hash, stable_hash

from horseracing_training.recipe import ModelRecipe


def test_feature_schema_hash_same_across_arms_same_version():
    schema = ["venue_code", "distance", "age"]  # same feature schema for both arms
    arm_a = stable_hash({"feature_version": "features-017", "cols": schema})
    arm_b = stable_hash({"feature_version": "features-017", "cols": schema})
    assert arm_a == arm_b  # 全arm同一 for calib-split arms sharing a version


def test_recipe_hash_is_arm_specific_but_reproducible():
    a1 = ModelRecipe(objective="pl_topk", calibration="isotonic", calib_frac=0.3).recipe_hash()
    a2 = ModelRecipe(objective="pl_topk", calibration="isotonic", calib_frac=0.3).recipe_hash()
    b = ModelRecipe(objective="pl_topk", calibration="isotonic", calib_frac=0.1).recipe_hash()
    assert a1 == a2       # within-arm reproducible
    assert a1 != b        # A (0.3) vs B (0.1) are different arms


def test_race_set_hash_matches_for_identical_race_sets():
    # both arms must see the same model-blind race set -> identical hash (FR-003)
    assert race_set_hash(["r1", "r2", "r3"]) == race_set_hash(["r3", "r2", "r1"])


def test_hash_contract_holds_six_distinct_fields():
    hc = HashContract(
        feature_schema_hash="fs", raw_matrix_content_hash="rm",
        model_race_set_hash="mrs", calib_race_set_hash="crs",
        transformed_matrix_hash="tm", model_artifact_hash="ma",
    )
    assert len(hc.to_dict()) == 6
