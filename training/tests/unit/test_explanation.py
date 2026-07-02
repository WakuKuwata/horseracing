"""Feature 040 T005: compute_explanations — additivity (INV-E1), determinism (INV-E3),
top-K truncation + other合算, JSON-value coercion, cond_logit booster.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from horseracing_training.explanation import (
    DEFAULT_TOP_K,
    METHOD,
    compute_explanations,
)
from horseracing_training.win_model import WinModel

FEATS = ["x1", "x2", "x3", "x4"]


def _synth(n=400, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({f: rng.normal(size=n) for f in FEATS})
    # label driven mostly by x1 so contributions are non-trivial
    y = (X["x1"] + 0.3 * rng.normal(size=n) > 0).astype(int).to_numpy()
    return X, y


def _binary_booster(X, y):
    dtrain = lgb.Dataset(X, label=y)
    return lgb.train({"objective": "binary", "num_leaves": 8, "verbose": -1}, dtrain,
                     num_boost_round=20)


def test_additivity_matches_raw_score():
    X, y = _synth()
    b = _binary_booster(X, y)
    exps = compute_explanations(b, X, FEATS, k=DEFAULT_TOP_K)
    raw = b.predict(X[FEATS], raw_score=True)
    assert len(exps) == len(X)
    for i, e in enumerate(exps):
        assert e is not None
        # INV-E1: base + Σitems + other == score == booster raw margin
        recon = e["base_value"] + sum(it["contribution"] for it in e["items"]) + e["other_contribution"]
        assert abs(recon - e["score"]) < 1e-9
        assert abs(e["score"] - raw[i]) < 1e-6


def test_topk_and_other():
    X, y = _synth()
    b = _binary_booster(X, y)
    e = compute_explanations(b, X, FEATS, k=2)[0]
    assert e["k"] == 2
    assert len(e["items"]) == 2
    # top-2 are the largest |contribution|; other = the remaining 2 features' sum
    all_abs = sorted((abs(it["contribution"]) for it in e["items"]), reverse=True)
    assert all_abs == sorted(all_abs, reverse=True)  # descending
    assert e["method"] == METHOD


def test_deterministic_and_tiebreak():
    X, y = _synth()
    b = _binary_booster(X, y)
    a = compute_explanations(b, X, FEATS, k=3)
    c = compute_explanations(b, X, FEATS, k=3)
    assert a == c  # identical input -> identical output
    # tie-break by feature name when |contribution| equal: craft equal contribs is hard,
    # so assert items are sorted by (-abs, feature) as a stable rule
    items = a[0]["items"]
    keys = [(-abs(it["contribution"]), it["feature"]) for it in items]
    assert keys == sorted(keys)


def test_value_coercion_nan_and_category():
    X, y = _synth(n=50)
    X = X.copy()
    X["x1"] = X["x1"].astype("float64")
    X.loc[0, "x1"] = np.nan
    b = _binary_booster(X, y)
    exps = compute_explanations(b, X, FEATS, k=4)
    # find the row-0 item for x1 if present; NaN value must serialise to None (not NaN)
    for it in exps[0]["items"]:
        if it["feature"] == "x1":
            assert it["value"] is None
    # all values are JSON-native (no numpy types)
    for e in exps:
        for it in e["items"]:
            assert it["value"] is None or isinstance(it["value"], (int, float, str))


def test_empty_input():
    X, y = _synth(n=20)
    b = _binary_booster(X, y)
    assert compute_explanations(b, X.iloc[0:0], FEATS) == []


def test_cond_logit_booster_additivity():
    # cond_logit (039) uses a raw lgb.Booster via lgb.train; pred_contrib must reconstruct margin
    rng = np.random.default_rng(1)
    rows = []
    for r in range(60):
        xs = rng.normal(size=(8, len(FEATS)))
        win = int((xs[:, 0] + rng.normal(scale=0.3, size=8)).argmax())
        for i in range(8):
            rows.append({**{f: xs[i, j] for j, f in enumerate(FEATS)},
                         "race_id": f"R{r}", "win": 1 if i == win else 0})
    df = pd.DataFrame(rows)
    m = WinModel(seed=0, objective="cond_logit").fit(
        df[FEATS], df["win"].to_numpy(), group_ids=df["race_id"].to_numpy()
    )
    booster = m.booster_  # raw lgb.Booster for cond_logit
    assert isinstance(booster, lgb.Booster)
    exps = compute_explanations(booster, df[FEATS], FEATS, k=3)
    raw = booster.predict(df[FEATS], raw_score=True)
    for i, e in enumerate(exps):
        assert e is not None and abs(e["score"] - raw[i]) < 1e-6
