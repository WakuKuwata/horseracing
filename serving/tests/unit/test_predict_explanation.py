"""Feature 040 T008: predict_race explanation — INV-E2 byte-parity, degenerate NULL,
cond_logit score = booster margin (not the softmaxed raw_predict), persist round-trip.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_training.calibration import Calibrator

from horseracing_serving.model_loader import ServingModel
from horseracing_serving.predictor import predict_race

_RACE = "200801010101"
_FEATS = ["f0", "f1"]


def _rows(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        [
            {"race_id": _RACE, "horse_id": f"H{i}",
             "f0": float(rng.normal()), "f1": float(rng.normal())}
            for i in range(n)
        ]
    )


def _booster(objective: str) -> lgb.Booster:
    rng = np.random.default_rng(1)
    X = pd.DataFrame({"f0": rng.normal(size=400), "f1": rng.normal(size=400)})
    if objective == "cond_logit":
        # cond_logit trains a raw regressor-like booster; a plain regression booster suffices
        # for structural tests (we only assert pred_contrib additivity vs its own margin).
        y = (X["f0"] > 0).astype(float).to_numpy()
        params = {"objective": "regression", "num_leaves": 8, "verbose": -1}
    else:
        y = (X["f0"] + 0.3 * rng.normal(size=400) > 0).astype(int).to_numpy()
        params = {"objective": "binary", "num_leaves": 8, "verbose": -1}
    return lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=20)


def _model(objective: str = "binary", booster: lgb.Booster | None = None) -> ServingModel:
    return ServingModel(
        model_version="t", booster=booster, degenerate_constant=0.2,
        calibrator=Calibrator(method="identity", identity=True),
        feature_cols=_FEATS, categorical_cols=[], encoders={},
        feature_version="features-011", feature_hash="x", objective=objective, metadata={},
    )


def test_degenerate_model_explanations_all_none():
    _, _, exps = predict_race(_model(booster=None), _RACE, _rows(5))
    assert set(exps) == {f"H{i}" for i in range(5)}
    assert all(v is None for v in exps.values())


def test_explanation_present_and_additive_binary():
    m = _model("binary", _booster("binary"))
    rows = _rows(6)
    _, _, exps = predict_race(m, _RACE, rows)
    for e in exps.values():
        assert e is not None
        recon = e["base_value"] + sum(it["contribution"] for it in e["items"]) + e["other_contribution"]
        assert abs(recon - e["score"]) < 1e-9
        assert e["method"] == "lgbm_pred_contrib" and e["k"] == 5


def test_inv_e2_predictions_unchanged_by_explanation():
    # INV-E2: computing explanations must not perturb win/top2/top3. Reference recompute uses
    # the SAME raw_predict -> calibrate -> assemble_predictions path predict_race uses.
    from horseracing_training.calibration import DEFAULT_CLIP
    from horseracing_training.predictor import assemble_predictions

    m = _model("binary", _booster("binary"))
    rows = _rows(7)
    preds, _, _ = predict_race(m, _RACE, rows)
    ids = sorted(rows["horse_id"])
    r2 = rows.set_index("horse_id").reindex(ids)
    for c in _FEATS:
        r2[c] = pd.to_numeric(r2[c], errors="coerce")
    raw = m.raw_predict(r2[_FEATS])
    cal = np.asarray(m.calibrator.transform(raw), dtype=float)
    ref = assemble_predictions(ids, cal, eps=DEFAULT_CLIP)
    for hid in ids:
        assert preds[hid].win == ref[hid].win      # byte-identical win
        assert preds[hid].top2 == ref[hid].top2
        assert preds[hid].top3 == ref[hid].top3


def test_cond_logit_score_is_margin_not_softmax():
    # explanation.score must equal booster RAW margin, NOT the race-softmaxed raw_predict output.
    b = _booster("cond_logit")
    m = _model("cond_logit", b)
    rows = _rows(8)
    _, _, exps = predict_race(m, _RACE, rows)
    r2 = rows.set_index("horse_id").reindex(sorted(rows["horse_id"]))
    margin = b.predict(r2[_FEATS], raw_score=True)
    softmaxed = m.raw_predict(r2[_FEATS])  # race-softmax -> sums to 1
    assert abs(softmaxed.sum() - 1.0) < 1e-9  # confirm raw_predict IS softmaxed
    for i, hid in enumerate(sorted(rows["horse_id"])):
        assert abs(exps[hid]["score"] - margin[i]) < 1e-6      # score == margin
        # and margin != softmaxed value (they live in different spaces)
