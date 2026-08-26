"""Feature 101: recency weight の適用範囲を暗黙の既定にしないためのテスト。"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest
from horseracing_eval.predictor import HorseEntry, RaceContext

from horseracing_training.dataset import RACE_DATE, RANK_LABEL, WIN_LABEL, TrainingMatrix
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe
from horseracing_training.win_model import WinModel


def test_recipe_rejects_enabled_recency_without_weight_scope() -> None:
    """recipe 段階で未宣言 scope を拒否し、異なる学習世界の暗黙混在を防ぐ。"""
    with pytest.raises(ValueError, match="weight_scope"):
        ModelRecipe(recency_half_life_days=730)


def test_predictor_rejects_enabled_recency_without_weight_scope() -> None:
    """recipe を迂回した直接生成も fail-closed にし、booster-only の暗黙既定を防ぐ。"""
    with pytest.raises(ValueError, match="weight_scope"):
        LightGBMPredictor(session=None, recency_half_life_days=730)


def test_explicit_booster_only_scope_is_accepted() -> None:
    """US1 の適用範囲を明示すれば構築でき、未宣言と明示宣言を区別できるようにする。"""
    recipe = ModelRecipe(
        recency_half_life_days=730,
        weight_scope="booster_only",
    )
    predictor = LightGBMPredictor(
        session=None,
        recency_half_life_days=730,
        weight_scope="booster_only",
    )

    assert recipe.weight_scope == "booster_only"
    assert predictor.weight_scope == "booster_only"


def test_declared_scope_is_recorded_in_fit_info(monkeypatch) -> None:
    """artifact に scope を残し、後から重みの実適用範囲を監査できない事故を防ぐ。"""
    def fake_fit(self, model_x, y_model, **kwargs):
        self.feature_cols_ = list(model_x.columns)
        self.booster_ = object()
        return self

    monkeypatch.setattr(WinModel, "fit", fake_fit)

    dates = [datetime.date(2022, 1, 1), datetime.date(2024, 1, 1)]
    frame = pd.DataFrame(
        {
            "race_id": ["old", "old", "new", "new"],
            "horse_id": ["o1", "o2", "n1", "n2"],
            RACE_DATE: [dates[0], dates[0], dates[1], dates[1]],
            WIN_LABEL: [1, 0, 1, 0],
            RANK_LABEL: [1, 2, 1, 2],
            "signal": [1.0, 0.0, 1.0, 0.0],
        }
    )
    contexts = [
        RaceContext(
            race_id=race_id,
            race_date=race_date,
            started_horses=(HorseEntry(f"{race_id}1"), HorseEntry(f"{race_id}2")),
        )
        for race_id, race_date in zip(("old", "new"), dates, strict=True)
    ]
    predictor = LightGBMPredictor(
        session=None,
        objective="pl_topk",
        calibration="none",
        calib_frac=0.0,
        recency_half_life_days=730,
        weight_scope="booster_only",
    )
    predictor._data = TrainingMatrix(
        frame=frame,
        feature_cols=["signal"],
        categorical_cols=[],
    )

    predictor.fit(contexts)

    assert predictor.fit_info_["recency"]["scope"] == {"declared": "booster_only"}
