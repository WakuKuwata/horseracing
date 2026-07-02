"""Feature 039: serving raw_predict applies race-softmax for cond_logit objective (DB-free)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_eval.consistency import check_consistency
from horseracing_training.calibration import Calibrator

from horseracing_serving.model_loader import ServingModel
from horseracing_serving.predictor import predict_race

_RACE = "200801010101"


class _FakeBooster:
    """Returns fixed raw margins (order = X rows) — stands in for a cond_logit lgb.Booster."""

    def __init__(self, raw):
        self._raw = np.asarray(raw, dtype=float)

    def predict(self, X):
        return self._raw[: len(X)]


def _model(raw, objective):
    return ServingModel(
        model_version="t", booster=_FakeBooster(raw), degenerate_constant=0.0,
        calibrator=Calibrator(method="identity", identity=True),
        feature_cols=["age"], categorical_cols=[], encoders={},
        feature_version="features-011", feature_hash="x", objective=objective, metadata={},
    )


def _rows(n):
    return pd.DataFrame([{"race_id": _RACE, "horse_id": f"H{i}", "age": 3} for i in range(n)])


def test_cond_logit_raw_predict_is_race_softmax():
    raw = [2.0, 1.0, 0.0, -1.0]
    m = _model(raw, "cond_logit")
    p = m.raw_predict(_rows(4))
    assert abs(p.sum() - 1.0) < 1e-9           # softmax over the whole race
    expected = np.exp(raw - np.max(raw))
    expected /= expected.sum()
    assert np.allclose(p, expected)
    assert p[0] > p[1] > p[2] > p[3]           # higher margin -> higher prob


def test_pl_topk_raw_predict_is_race_softmax():
    # Feature 042: pl_topk shares the cond_logit race-softmax postprocess
    raw = [1.0, 0.0, -1.0]
    m = _model(raw, "pl_topk")
    p = m.raw_predict(_rows(3))
    assert abs(p.sum() - 1.0) < 1e-9
    assert p[0] > p[1] > p[2]


def test_binary_raw_predict_unchanged():
    raw = [0.4, 0.3, 0.1]
    m = _model(raw, "binary")
    p = m.raw_predict(_rows(3))
    assert np.allclose(p, raw)                  # no softmax for binary


def test_cond_logit_predict_race_consistent():
    m = _model([1.5, 0.5, -0.5, 0.0, 0.2], "cond_logit")
    preds, _, _ = predict_race(m, _RACE, _rows(5))
    check_consistency(preds)
    assert abs(sum(p.win for p in preds.values()) - 1.0) < 1e-9
