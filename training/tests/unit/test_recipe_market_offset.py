"""T003t: ModelRecipe fail-closes on market_offset=true (FR-019, codex C3, analyze G1)."""

from __future__ import annotations

import pytest

from horseracing_training.recipe import MarketOffsetForbidden, ModelRecipe


def test_market_offset_true_is_rejected_fail_closed():
    with pytest.raises(MarketOffsetForbidden):
        ModelRecipe(market_offset=True)


def test_market_offset_false_is_allowed():
    r = ModelRecipe(market_offset=False)
    assert r.market_offset is False


def test_default_recipe_has_market_offset_false():
    assert ModelRecipe().market_offset is False


def test_recipe_hash_is_deterministic_and_meta_is_plain_dict():
    r = ModelRecipe(objective="pl_topk", calibration="isotonic")
    assert r.recipe_hash() == ModelRecipe(objective="pl_topk", calibration="isotonic").recipe_hash()
    meta = r.meta()
    assert isinstance(meta, dict) and meta["objective"] == "pl_topk"
    # different recipe -> different hash
    assert r.recipe_hash() != ModelRecipe(calibration="none").recipe_hash()
