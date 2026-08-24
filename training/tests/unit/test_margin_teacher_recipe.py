"""レシピ hash の back-compat 常設ガード(099 T002 起源・REJECT 後も保全)。

SNAPSHOT 定数は 2026-08-24(6477b7b 時点)の実測値。ModelRecipe に新フィールドを足す
ときは NEW_HASH_DEFAULT_OMISSIONS に default 省略を登録しない限りこのテストが赤くなる —
CalibSplitFactory は meta() 全体を hash するため、素朴なフィールド追加は arm E 系の既存
hash を全て変える(099 codex P0-1 で実証・099 REJECT 後も機構と本ガードは常設)。
"""

from __future__ import annotations

from horseracing_training.calib_split import (
    CalibSplitFactory,
)
from horseracing_training.recipe import ModelRecipe

# --- 変更前の実測値(2026-08-24・6477b7b)。書き換え禁止 — 変わったら back-compat が壊れた
_SNAPSHOT_RECIPE_HASH = {
    "default": "f8861dd93cc5371366354ad41de622041db319f46cf5379228f78eda5ef0d7bc",
    "prod_holdout": "f4f6c64a7bcad124aeec845786bd84d2470e9f7f8830ee2de5e8a18934cd979b",
    "drop_arm": "ace5a31bafcc81ffb03c09c168af633193b35eac5da0d7ad1680dd7848c4809f",
    "day_split": "f812430901490ed44dbda4359dbba3c98cda44e1fbf67aa4890bbc0ed3262cbd",
}
_SNAPSHOT_FACTORY_HASH = {
    ("default", "isotonic"): "c303d21199849ad1043c8ccb07f54e84435f516a4b823896ed77a94f69f34944",
    ("default", "power"): "fe656cb71e2a77b05b7c5a2ff8104bdeeaed9d41f55cda41edfae5ca58fdc08a",
    ("prod_holdout", "isotonic"): "6e2f0a2f17b13dce4b5a45a42bdaebfd4e8e010fcb27025cfa27ea5e275845e6",
    ("prod_holdout", "power"): "940f84f1b9ba3f2ff993e38d187a05045e175fc66ee29ff539a88def46e38ec5",
    ("drop_arm", "isotonic"): "c32c6c764036a2e8b293b5fcec6be8ffe0a1f063664edb70449cdd26752275c6",
    ("drop_arm", "power"): "0c0772d4f6f0694c253ac3b37b6a795bc3f9136e9e456940396ed458a7c623e7",
    ("day_split", "isotonic"): "1a1be3acf1a7aa289b69c09d5e3fdc5b567abe2a0eccc40fd1af1c090406aeb9",
    ("day_split", "power"): "47ae0dd6e8669198735ef23affc97e91ecb115ed598b4e2f1c10b36d3df39cbd",
}


def _recipes() -> dict[str, ModelRecipe]:
    return {
        "default": ModelRecipe(),
        "prod_holdout": ModelRecipe(
            objective="pl_topk", calibration="isotonic", calib_frac=0.3,
            weight_mask_rate=0.5, weight_mask_seed=20260810,
            params=(("n_estimators", 900),),
        ),
        "drop_arm": ModelRecipe(drop_features=("jockey_win_rate",)),
        "day_split": ModelRecipe(calibration_split_unit="race_day_v1"),
    }


def test_recipe_hash_snapshot_is_stable():
    """RecipeFactory 系: 既存レシピの recipe_hash は 1 つも変わらない(INV-MT5)."""
    for name, recipe in _recipes().items():
        assert recipe.recipe_hash() == _SNAPSHOT_RECIPE_HASH[name], name


def test_calib_split_factory_hash_snapshot_is_stable():
    """arm E 系: CalibSplitFactory の recipe_hash も 1 つも変わらない(codex P0-1 の本丸)."""
    for name, recipe in _recipes().items():
        for method in ("isotonic", "power"):
            f = CalibSplitFactory(None, recipe, method=method, n_oof_blocks=8)
            assert f.recipe_hash == _SNAPSHOT_FACTORY_HASH[(name, method)], (name, method)
