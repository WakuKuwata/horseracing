"""容量(LightGBM params)をレシピで振れるようにした配線 — 2026-08-18。

現 active のハイパーパラメータは `DEFAULT_PARAMS` と 1 文字も違わなかった。objective が
`binary` だった頃の既定で、その後 objective も特徴数も booster の学習範囲も変わったのに
容量だけが据え置かれていた。スクリーニングで rounds x3 が -0.0066 CI[-0.0099, -0.0034] と
出たので、確認を arm E レシピで回せるようにする。

**後方互換が要件**: 上書きの無いレシピは `recipe_hash` が 1 ビットも動いてはいけない。
動くと過去の model_version の同一性が壊れる。
"""

from __future__ import annotations

import dataclasses

import pytest

from horseracing_training.recipe import ModelRecipe
from horseracing_training.win_model import DEFAULT_PARAMS


def test_absent_params_do_not_change_the_recipe_hash():
    """073 の split unit・079 の ev_weight・091 の weight mask と同じ省略規約に従う。"""
    assert ModelRecipe().recipe_hash() == ModelRecipe(params=None).recipe_hash()


def test_params_enter_the_hash_when_set():
    """容量はモデルの同一性そのもの: 900 本の木のモデルは 300 本のモデルと別物である。"""
    base = ModelRecipe()
    bigger = ModelRecipe(params=(("n_estimators", 900),))
    assert bigger.recipe_hash() != base.recipe_hash()
    assert bigger.recipe_hash() != ModelRecipe(params=(("n_estimators", 1800),)).recipe_hash()


def test_resolved_params_merges_onto_the_defaults():
    assert ModelRecipe().resolved_params() is None  # 上書き無し = 既定のまま(挙動不変)
    got = ModelRecipe(params=(("n_estimators", 900), ("colsample_bytree", 0.7))).resolved_params()
    assert got["n_estimators"] == 900
    assert got["colsample_bytree"] == 0.7
    for k, v in DEFAULT_PARAMS.items():          # 触っていない欄は既定のまま
        if k not in ("n_estimators", "colsample_bytree"):
            assert got[k] == v


def test_params_is_immutable_so_a_frozen_recipe_stays_frozen():
    r = ModelRecipe(params=(("n_estimators", 900),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.params = (("n_estimators", 300),)
    assert isinstance(r.params, tuple)


def test_arm_e_forwards_params_and_the_field_guard_knows_about_them():
    """`_check_recipe_fields_accounted_for` は、レシピに欄が増えたのに arm E が黙って落とす
    事故(091 の weight_mask がまさにそれ)を防ぐためにある。params を足したので台帳にも要る。"""
    from horseracing_training.calib_split import OofCalibratedPredictor

    disposition = OofCalibratedPredictor._RECIPE_FIELD_DISPOSITION
    assert disposition.get("params") == "forward"
    assert not {f.name for f in dataclasses.fields(ModelRecipe())} - set(disposition)
