"""US1 (SC-001): predict_race output is consistency-passing, even small fields; NaN != 0."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.consistency import check_consistency
from horseracing_training.calibration import Calibrator

from horseracing_serving.model_loader import ServingModel
from horseracing_serving.predictor import predict_race

_RACE = "200801010101"


def _model(const: float = 0.2) -> ServingModel:
    # booster=None -> degenerate constant raw; identity calibrator just clips. DB-free.
    return ServingModel(
        model_version="t", booster=None, degenerate_constant=const,
        calibrator=Calibrator(method="identity", identity=True),
        feature_cols=["age", "venue_code"], categorical_cols=["venue_code"], encoders={},
        feature_version="features-004", feature_hash="x", metadata={},
    )


def _rows(n: int, *, with_nan: bool = False) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"race_id": _RACE, "horse_id": f"H{i}",
             "age": (np.nan if (with_nan and i == 0) else 3 + i % 3), "venue_code": "05"}
            for i in range(n)
        ]
    )


@pytest.mark.parametrize("n", [2, 3, 5, 8, 16])
def test_consistency_various_field_sizes(n):
    preds, _ = predict_race(_model(), _RACE, _rows(n))
    check_consistency(preds)  # 0<=win<=top2<=top3<=1, Σ within tolerance (target min(k,N))
    assert set(preds) == {f"H{i}" for i in range(n)}  # every started horse predicted


def test_nan_feature_snapshot_is_none_not_zero():
    _, snaps = predict_race(_model(), _RACE, _rows(5, with_nan=True))
    assert snaps["H0"]["age"] is None  # debut/missing stays Unknown, not 0
    assert "_raw_win" in snaps["H0"] and "_calibrated_win" in snaps["H0"]
