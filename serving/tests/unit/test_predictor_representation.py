"""Feature 098: serving-time race_class representation and audit."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest
from horseracing_training.calibration import Calibrator

from horseracing_serving import predictor
from horseracing_serving.model_loader import ServingError, ServingModel
from horseracing_serving.pipeline import _base_logic_version
from horseracing_serving.predictor import predict_race

_RACE = "202605010101"


class _SpyBooster:
    def __init__(self) -> None:
        self.frames: list[pd.DataFrame] = []

    def predict(self, frame, **kwargs):
        self.frames.append(frame.copy())
        if kwargs.get("pred_contrib"):
            raise RuntimeError("explanations are outside this spy's contract")
        return np.full(len(frame), 0.2, dtype=float)


def _model(
    representation: str,
    vocab: list[str],
    *,
    feature_version: str,
) -> tuple[ServingModel, _SpyBooster]:
    booster = _SpyBooster()
    return (
        ServingModel(
            model_version="fixture",
            booster=booster,
            degenerate_constant=0.0,
            calibrator=Calibrator(method="identity", identity=True),
            feature_cols=["race_class"],
            categorical_cols=["race_class"],
            feature_version=feature_version,
            feature_hash="fixture-hash",
            race_class_representation=representation,
            categorical_vocab={"race_class": vocab},
        ),
        booster,
    )


def _rows(*values: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"race_id": _RACE, "horse_id": f"H{i:03d}", "race_class": value}
            for i, value in enumerate(values)
        ]
    )


def test_canonical_023_maps_full_width_before_booster():
    model, booster = _model("canonical-v1", ["1勝"], feature_version="features-023")

    predict_race(model, _RACE, _rows("１勝", "１勝", "１勝"))

    assert booster.frames[0]["race_class"].tolist() == ["1勝", "1勝", "1勝"]


def test_raw_021_leaves_full_width_value_unchanged():
    model, booster = _model("raw", ["１勝"], feature_version="features-021")

    predict_race(model, _RACE, _rows("１勝", "１勝", "１勝"))

    assert booster.frames[0]["race_class"].tolist() == ["１勝", "１勝", "１勝"]


def test_out_of_vocab_value_is_audited_without_blocking_prediction():
    model, _ = _model("canonical-v1", ["1勝"], feature_version="features-023")

    predictions, _, _, audit = predict_race(model, _RACE, _rows("1勝", "４勝", "1勝"))

    assert len(predictions) == 3
    assert audit["n_unknown"] == 1
    assert audit["unknown_values"] == ["４勝"]
    assert audit["n_rows"] == 3


def test_unknown_rate_over_one_percent_logs_warning(caplog):
    model, _ = _model("canonical-v1", ["1勝"], feature_version="features-023")

    with caplog.at_level(logging.WARNING, logger=predictor.__name__):
        predict_race(model, _RACE, _rows("1勝", "４勝", "1勝"))

    assert "race_class" in caplog.text
    assert "unknown" in caplog.text


def test_model_without_vocab_reports_unavailable_audit_without_warning(caplog):
    model, _ = _model("raw", [], feature_version="features-021")

    with caplog.at_level(logging.WARNING, logger=predictor.__name__):
        _, _, _, audit = predict_race(model, _RACE, _rows("１勝", "４勝", "１勝"))

    assert audit["n_unknown"] is None
    assert not caplog.records


def test_nan_increase_across_representation_and_category_coercion_fails_closed(monkeypatch):
    model, _ = _model("canonical-v1", ["1勝"], feature_version="features-023")

    def _inject_nan(series):
        result = series.copy()
        result.iloc[0] = np.nan
        return result, {"mapped": {}, "out_of_table": {}}

    monkeypatch.setattr(predictor, "canonicalise", _inject_nan)

    with pytest.raises(ServingError, match="NaN"):
        predict_race(model, _RACE, _rows("１勝", "１勝", "１勝"))


def test_logic_version_carries_representation_marker():
    model, _ = _model("canonical-v1", ["1勝"], feature_version="features-023")

    assert ";rcr=canonical-v1" in _base_logic_version(model)
