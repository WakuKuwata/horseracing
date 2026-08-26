"""Feature 101: recency weight が学習境界へ正しく届くことを固定するテスト。"""

from __future__ import annotations

import datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.predictor import HorseEntry, RaceContext

from horseracing_training.calibration import Calibrator
from horseracing_training.dataset import (
    RACE_DATE,
    RANK_LABEL,
    WIN_LABEL,
    TrainingMatrix,
)
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.target_encoding import TargetEncoder
from horseracing_training.win_model import WinModel

# --- feature 101 は REJECT(2026-08-27)。fit 経路は非結線保全 ---------------------------------
#
# 判定は winner NLL の paired 差 **+0.005760**(sample CI [+0.003056, +0.008308] /
# total CI [+0.002778, +0.008601])で、CI がゼロを跨がず**有意に悪化**した。半減期 730 日は
# 実効標本数を 958,011 → 409,831(42.8%)に落とすので、学習行を捨てた損失が直近を重くした
# 利得を上回ったと読める。
#
# 「fit 経路に重みが届くこと」を検証する本ファイルは、結線を外した以上そのままでは成立しない。
# **削除せず skip で残す** — 時間重みを別の形で再事前登録するときに、何を検証すべきだったかが
# そのまま使えるからである(062/070/090/100-US3 と同じ非結線保全の規律)。重み計算そのもの
# (`recency.py`)の単体テストは**結線と無関係に緑**のまま残っている。
pytestmark = pytest.mark.skip(
    reason="feature 101 REJECT: fit 経路を非結線保全したため(spec の実測結果を参照)"
)



def _matrix() -> TrainingMatrix:
    rows: list[dict[str, Any]] = []
    origin = datetime.date(2020, 1, 1)
    for race_index in range(8):
        race_id = f"R{race_index:02d}"
        race_date = origin + datetime.timedelta(days=180 * race_index)
        for horse_index in range(3):
            rows.append(
                {
                    "race_id": race_id,
                    "horse_id": f"H{race_index:02d}-{horse_index}",
                    RACE_DATE: race_date,
                    WIN_LABEL: int(horse_index == 0),
                    RANK_LABEL: horse_index + 1,
                    "signal": float(race_index - horse_index),
                    "jockey_id": f"J{horse_index}",
                }
            )
    frame = pd.DataFrame(rows)
    frame["jockey_id"] = frame["jockey_id"].astype("category")
    return TrainingMatrix(
        frame=frame,
        feature_cols=["signal", "jockey_id"],
        categorical_cols=["jockey_id"],
    )


def _contexts(matrix: TrainingMatrix) -> list[RaceContext]:
    contexts = []
    for race_id, group in matrix.frame.groupby("race_id", sort=True):
        contexts.append(
            RaceContext(
                race_id=str(race_id),
                race_date=group[RACE_DATE].iloc[0],
                started_horses=tuple(
                    HorseEntry(horse_id=str(horse_id)) for horse_id in group["horse_id"]
                ),
            )
        )
    return contexts


def _spy_win_model(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_fit(self, model_x, y_model, **kwargs):
        self.feature_cols_ = list(model_x.columns)
        self.booster_ = object()
        calls.append(
            {
                "model_x": model_x.copy(),
                "y_model": np.asarray(y_model).copy(),
                **{
                    name: value.copy() if isinstance(value, np.ndarray) else value
                    for name, value in kwargs.items()
                },
            }
        )
        return self

    def fake_predict(self, model_x, *, group_ids=None, offsets=None):
        return np.full(len(model_x), 0.5, dtype=float)

    monkeypatch.setattr(WinModel, "fit", fake_fit)
    monkeypatch.setattr(WinModel, "predict", fake_predict)
    return calls


def _fit(matrix: TrainingMatrix, **kwargs) -> LightGBMPredictor:
    predictor = LightGBMPredictor(
        session=None,
        objective="pl_topk",
        calibration="none",
        calib_frac=0.0,
        **kwargs,
    )
    predictor._data = matrix
    predictor.fit(_contexts(matrix))
    return predictor


def test_disabled_recency_matches_the_omitted_argument_path(monkeypatch) -> None:
    """None が WinModel へ余計な重みを渡さず、101 より前の fit をビット単位で守る。"""
    calls = _spy_win_model(monkeypatch)
    matrix = _matrix()

    omitted = _fit(matrix)
    explicit_none = _fit(matrix, recency_half_life_days=None)

    assert len(calls) == 2
    assert calls[0]["weights"] is None
    assert calls[1]["weights"] is None
    pd.testing.assert_frame_equal(calls[0]["model_x"], calls[1]["model_x"], check_exact=True)
    np.testing.assert_array_equal(calls[0]["y_model"], calls[1]["y_model"])
    np.testing.assert_array_equal(calls[0]["group_ids"], calls[1]["group_ids"])
    assert omitted.fit_info_ == explicit_none.fit_info_
    assert "recency" not in omitted.fit_info_


def test_enabled_recency_reaches_booster_with_normalized_race_weights(monkeypatch) -> None:
    """行重み総量とレース内定数を配線後も守り、正則化量や PL 尤度の破壊を防ぐ。"""
    calls = _spy_win_model(monkeypatch)
    matrix = _matrix()
    predictor = _fit(
        matrix,
        recency_half_life_days=730,
        weight_scope="booster_only",
    )

    call = calls[0]
    weights = call["weights"]
    race_ids = call["group_ids"]
    assert weights is not None
    assert np.isclose(float(np.sum(weights)), len(call["model_x"]), rtol=1e-9, atol=0.0)
    for race_id in np.unique(race_ids):
        race_weights = weights[race_ids == race_id]
        assert np.all(race_weights == race_weights[0])

    audit = predictor.fit_info_["recency"]
    assert audit["cutoff"] == max(matrix.frame[RACE_DATE]).isoformat()
    assert audit["half_life_days"] == 730
    assert audit["ess_total"] > 0.0
    assert audit["weight_sum"] == len(call["model_x"])


def test_booster_only_scope_does_not_weight_encoder_or_calibrator(monkeypatch) -> None:
    """US1 の重みを booster だけに限定し、TE・校正器へ未設計の重みが漏れるのを防ぐ。"""
    booster_calls = _spy_win_model(monkeypatch)
    te_calls: list[dict[str, Any]] = []
    oof_te_calls: list[dict[str, Any]] = []
    calibrator_calls: list[dict[str, Any]] = []

    def fake_fit_target_encoder(frame, col, **kwargs):
        te_calls.append(dict(kwargs))
        return TargetEncoder(
            col=col,
            prior=kwargs["prior"],
            mapping={},
            smoothing=kwargs["smoothing"],
        )

    def fake_oof_target_encode(frame, col, **kwargs):
        oof_te_calls.append(dict(kwargs))
        return pd.Series(np.full(len(frame), kwargs["prior"]), index=frame.index)

    def fake_fit_calibrator(raw, y, **kwargs):
        calibrator_calls.append(dict(kwargs))
        return Calibrator(method="identity", clip=kwargs["clip"], identity=True)

    monkeypatch.setattr(
        "horseracing_training.predictor.fit_target_encoder",
        fake_fit_target_encoder,
    )
    monkeypatch.setattr(
        "horseracing_training.predictor.oof_target_encode",
        fake_oof_target_encode,
    )
    monkeypatch.setattr(
        "horseracing_training.predictor.fit_calibrator",
        fake_fit_calibrator,
    )

    matrix = _matrix()
    predictor = LightGBMPredictor(
        session=None,
        objective="pl_topk",
        calibration="isotonic",
        calib_frac=0.25,
        target_encode_cols=("jockey_id",),
        recency_half_life_days=730,
        weight_scope="booster_only",
    )
    predictor._data = matrix
    predictor.fit(_contexts(matrix))

    assert booster_calls[0]["weights"] is not None
    assert te_calls and oof_te_calls and calibrator_calls
    for call in [*te_calls, *oof_te_calls, *calibrator_calls]:
        assert "weights" not in call
        assert "sample_weight" not in call
    assert predictor.fit_info_["recency"]["scope"] == {"declared": "booster_only"}
