"""Feature 079 (step 3): ModelRecipe ev_weight knob + hash back-compat + fail-closed factory."""

from __future__ import annotations

import pytest

from horseracing_training.recipe import ModelRecipe, RecipeFactory


def test_ev_weight_defaults_off():
    assert ModelRecipe().ev_weight is False


def test_default_recipe_hash_unchanged_by_new_field():
    """ev_weight=False must be OMITTED from the hash so every pre-079 recipe is byte-identical."""
    r = ModelRecipe(objective="pl_topk", calibration="isotonic")
    assert r.ev_weight is False
    # meta carries the field (audit) but the hash drops it when off
    assert "ev_weight" in r.meta()
    r_explicit_off = ModelRecipe(objective="pl_topk", calibration="isotonic", ev_weight=False)
    assert r.recipe_hash() == r_explicit_off.recipe_hash()


def test_ev_weight_on_changes_hash():
    off = ModelRecipe(objective="pl_topk")
    on = ModelRecipe(objective="pl_topk", ev_weight=True)
    assert off.recipe_hash() != on.recipe_hash()


def test_factory_fail_closed_when_ev_weight_on_without_oof(monkeypatch):
    """recipe.ev_weight=True but no oof_p -> the predictor must refuse to fit (fail-closed).

    Constructing LightGBMPredictor raises before touching the DB, so no session is needed.
    """
    recipe = ModelRecipe(objective="pl_topk", ev_weight=True)
    factory = RecipeFactory(session=object(), recipe=recipe, oof_p=None)
    with pytest.raises(ValueError, match="requires a frozen OOF-p source"):
        factory.fit([])  # predictor ctor fails closed before any fitting


def test_factory_ev_weight_off_needs_no_oof():
    # off path: predictor constructs fine with oof_p=None (no fitting invoked here)
    recipe = ModelRecipe(objective="pl_topk", ev_weight=False)
    factory = RecipeFactory(session=object(), recipe=recipe, oof_p=None)
    assert factory.recipe.ev_weight is False
