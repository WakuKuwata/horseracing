"""Feature 049: predict_race stage-discount pass-through + logic_version recording (T022)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_eval.stage_discount import StageDiscount
from horseracing_training.calibration import Calibrator

from horseracing_serving.model_loader import ServingModel
from horseracing_serving.pipeline import _sdisc_lv
from horseracing_serving.predictor import predict_race

_RACE = "200801010101"


def _model() -> ServingModel:
    return ServingModel(
        model_version="t", booster=None, degenerate_constant=0.2,
        calibrator=Calibrator(method="identity", identity=True),
        feature_cols=["age", "venue_code"], categorical_cols=["venue_code"], encoders={},
        feature_version="features-004", feature_hash="x", metadata={},
    )


def _rows(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        [{"race_id": _RACE, "horse_id": f"H{i}", "age": 3 + i % 3, "venue_code": "05"}
         for i in range(n)]
    )


def test_none_and_identity_are_byte_identical():
    base, _, _ = predict_race(_model(), _RACE, _rows(8))
    ident, _, _ = predict_race(_model(), _RACE, _rows(8), stage_discount=StageDiscount())
    for h in base:
        assert base[h].win == ident[h].win
        assert base[h].top2 == ident[h].top2   # identity path == legacy, exact
        assert base[h].top3 == ident[h].top3


def test_discount_leaves_win_unchanged():
    # win is byte-identical regardless of the discount (INV-S2), even though the tail formula runs
    base, _, _ = predict_race(_model(), _RACE, _rows(8))
    disc, _, _ = predict_race(
        _model(), _RACE, _rows(8), stage_discount=StageDiscount(lambda2=0.7, lambda3=0.6)
    )
    for h in base:
        assert disc[h].win == base[h].win
        assert 0.0 <= disc[h].win <= disc[h].top2 <= disc[h].top3 <= 1.0 + 1e-9


def test_logic_version_fragment_appended():
    lv = "feat=features-012;serve=serve-0.1.0"
    assert _sdisc_lv(lv, None) == lv                       # no discount -> unchanged (compat)
    assert _sdisc_lv(lv, StageDiscount()) == f"{lv};sdisc=identity"
    sd = StageDiscount(lambda2=0.82, lambda3=0.70, n_races_l2=5000, n_races_l3=5000)
    out = _sdisc_lv(lv, sd)
    assert out.startswith(f"{lv};sdisc=harville;l2=0.82000;l3=0.70000;n2=5000;n3=5000")


def test_win_probs_sum_to_one_regardless_of_discount():
    for sd in (None, StageDiscount(lambda2=0.5, lambda3=0.5)):
        preds, _, _ = predict_race(_model(), _RACE, _rows(10), stage_discount=sd)
        assert abs(sum(p.win for p in preds.values()) - 1.0) < 1e-9
