"""Feature 101: recency の recipe identity と arm E 配線を固定するテスト。"""

from __future__ import annotations

import dataclasses

import pytest

from horseracing_training.calib_split import ArmNotServable, OofCalibratedPredictor
from horseracing_training.recipe import ModelRecipe

_PRE_101_DEFAULT_RECIPE_HASH = (
    "f8861dd93cc5371366354ad41de622041db319f46cf5379228f78eda5ef0d7bc"
)


def test_recency_half_life_field_defaults_to_none() -> None:
    """半減期の既定を無効に保ち、既存レシピへ意図せず時間重みが入る事故を防ぐ。"""
    fields = {field.name: field for field in dataclasses.fields(ModelRecipe)}

    assert fields["recency_half_life_days"].default is None
    assert ModelRecipe().recency_half_life_days is None


def test_recency_default_is_registered_for_new_hash_omission() -> None:
    """099 の省略台帳への登録を必須にし、arm E 系の既存 hash が全滅する事故を防ぐ。"""
    # 省略するキーは **2 つ**。`weight_scope` も一緒に落とさないと、recency 無効の
    # レシピに `weight_scope: None` が残って既存 hash が動く(実測: 片方だけにすると
    # test_margin_teacher_recipe の両系スナップショットが落ちる)。091 の
    # `weight_mask_rate -> (weight_mask_rate, weight_mask_seed)` と同じ形である。
    assert ModelRecipe.NEW_HASH_DEFAULT_OMISSIONS["recency_half_life_days"] == (
        None,
        ("recency_half_life_days", "weight_scope"),
    )


def test_default_recipe_hash_keeps_the_pre_101_snapshot() -> None:
    """recency 無効時の model identity を固定し、現 active 系譜の再同定を防ぐ。"""
    assert ModelRecipe().recipe_hash() == _PRE_101_DEFAULT_RECIPE_HASH
    assert ModelRecipe(recency_half_life_days=None).recipe_hash() == (
        _PRE_101_DEFAULT_RECIPE_HASH
    )


def test_enabled_recency_changes_recipe_identity() -> None:
    """半減期を有効にしたモデルを無重みモデルと別 hash にし、artifact の混同を防ぐ。"""
    weighted = ModelRecipe(
        recency_half_life_days=730,
        weight_scope="booster_only",
    )

    assert weighted.recipe_hash() != ModelRecipe().recipe_hash()


def test_arm_e_declares_recency_fields_as_forwarded() -> None:
    """arm E が半減期と scope を黙って落とし、レシピと異なる booster を作る事故を防ぐ。"""
    disposition = OofCalibratedPredictor._RECIPE_FIELD_DISPOSITION

    assert disposition["recency_half_life_days"] == "forward"
    assert disposition["weight_scope"] == "forward"


def test_arm_e_make_base_forwards_recency_configuration(monkeypatch) -> None:
    """台帳だけでなく constructor まで値を届け、arm E が無重みで学習する事故を防ぐ。"""
    captured = {}

    class SpyPredictor:
        def __init__(self, session, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "horseracing_training.calib_split.LightGBMPredictor",
        SpyPredictor,
    )
    recipe = ModelRecipe(
        recency_half_life_days=730,
        weight_scope="booster_only",
    )

    OofCalibratedPredictor(session=None, recipe=recipe)._make_base()

    assert captured["recency_half_life_days"] == 730
    assert captured["weight_scope"] == "booster_only"


def test_arm_e_field_guard_fails_closed_when_recency_is_unregistered(monkeypatch) -> None:
    """省略台帳から半減期が落ちた変異を拒否し、未知フィールドの黙殺を防ぐ。"""
    monkeypatch.delitem(
        OofCalibratedPredictor._RECIPE_FIELD_DISPOSITION,
        "recency_half_life_days",
    )
    predictor = OofCalibratedPredictor(session=None, recipe=ModelRecipe())

    with pytest.raises(ArmNotServable, match="recency_half_life_days"):
        predictor._check_recipe_fields_accounted_for()
