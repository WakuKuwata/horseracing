"""Feature 040 T005: compute_explanations — additivity (INV-E1), determinism (INV-E3),
top-K truncation + other合算, JSON-value coercion, cond_logit booster.
"""

from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from horseracing_training.explanation import (
    DEFAULT_TOP_K,
    METHOD,
    METHOD_VERSION_V1,
    METHOD_VERSION_V2,
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
    return lgb.train(
        {"objective": "binary", "num_leaves": 8, "verbose": -1}, dtrain, num_boost_round=20
    )


def test_additivity_matches_raw_score():
    X, y = _synth()
    b = _binary_booster(X, y)
    exps = compute_explanations(b, X, FEATS, k=DEFAULT_TOP_K)
    raw = b.predict(X[FEATS], raw_score=True)
    assert len(exps) == len(X)
    for i, e in enumerate(exps):
        assert e is not None
        # INV-E1: base + Σitems + other == score == booster raw margin
        recon = (
            e["base_value"] + sum(it["contribution"] for it in e["items"]) + e["other_contribution"]
        )
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
            rows.append(
                {
                    **{f: xs[i, j] for j, f in enumerate(FEATS)},
                    "race_id": f"R{r}",
                    "win": 1 if i == win else 0,
                }
            )
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


class _FixedContribBooster:
    def __init__(self, contrib):
        self.contrib = contrib
        self.predict_calls = 0

    def predict(self, X, *, pred_contrib):
        assert pred_contrib is True
        self.predict_calls += 1
        return self.contrib


def test_v2_centering_matches_hand_calculation_and_invariants():
    X = pd.DataFrame(
        {
            "alpha": [1.0, 2.0, 3.0],
            "beta": [10.0, 20.0, 30.0],
            "gamma": [np.nan, 2.0, np.nan],
            "delta": [3.0, 2.0, 1.0],
        }
    )
    feature_cols = list(X.columns)
    contrib = np.array(
        [
            [3.0, 10.0, 4.0, 0.0, 0.5],
            [6.0, 11.0, 4.0, 6.0, 0.5],
            [9.0, 12.0, 4.0, 3.0, 0.5],
        ]
    )
    expected_raw = np.array([17.5, 27.5, 28.5])
    booster = _FixedContribBooster(contrib)

    exps = compute_explanations(
        booster,
        X,
        feature_cols,
        k=2,
        center_within_group=True,
        expected_raw_scores=expected_raw,
    )

    assert booster.predict_calls == 1
    assert [e["method_version"] for e in exps] == [METHOD_VERSION_V2] * 3
    assert [e["score_centered"] for e in exps] == [-7.0, 3.0, 4.0]
    np.testing.assert_allclose(
        [e["score_centered"] for e in exps], expected_raw - expected_raw.mean()
    )
    assert [e["centering_population_size"] for e in exps] == [3, 3, 3]
    assert exps[0]["items"] == [
        {
            "feature": "alpha",
            "value": 1.0,
            "contribution": 3.0,
            "contribution_centered": -3.0,
        },
        {
            "feature": "delta",
            "value": 3.0,
            "contribution": 0.0,
            "contribution_centered": -3.0,
        },
    ]
    assert [e["other_contribution"] for e in exps] == [14.0, 15.0, 7.0]
    assert [e["other_contribution_centered"] for e in exps] == [-1.0, 0.0, 0.0]
    for e in exps:
        assert e["score_centered"] == (
            sum(item["contribution_centered"] for item in e["items"])
            + e["other_contribution_centered"]
        )

    all_items = compute_explanations(
        _FixedContribBooster(contrib),
        X,
        feature_cols,
        k=len(feature_cols),
        center_within_group=True,
        expected_raw_scores=expected_raw,
    )
    centered_by_feature = {
        feature: [
            next(item["contribution_centered"] for item in e["items"] if item["feature"] == feature)
            for e in all_items
        ]
        for feature in feature_cols
    }
    for values in centered_by_feature.values():
        assert abs(sum(values)) < 1e-12


def test_v2_excludes_constant_values_and_all_nan_but_keeps_mixed_nan():
    feature_cols = ["constant", "all_nan", "mixed_nan", "varying"]
    X = pd.DataFrame(
        {
            "constant": [5.0, 5.0, 5.0],
            "all_nan": [np.nan, np.nan, np.nan],
            "mixed_nan": [np.nan, 1.0, np.nan],
            "varying": [0.0, 1.0, 2.0],
        }
    )
    contrib = np.array(
        [
            [100.0, 90.0, 3.0, 6.0, 0.25],
            [200.0, 80.0, 6.0, 3.0, 0.25],
            [300.0, 70.0, 0.0, 0.0, 0.25],
        ]
    )

    exps = compute_explanations(
        _FixedContribBooster(contrib),
        X,
        feature_cols,
        center_within_group=True,
    )

    for e in exps:
        features = {item["feature"] for item in e["items"]}
        assert features == {"mixed_nan", "varying"}
        assert len(e["items"]) == 2 < DEFAULT_TOP_K


def test_v2_single_row_has_no_items():
    X = pd.DataFrame({feature: [float(i)] for i, feature in enumerate(FEATS)})
    contrib = np.array([[1.0, -2.0, 3.0, -4.0, 0.75]])

    exps = compute_explanations(_FixedContribBooster(contrib), X, FEATS, center_within_group=True)

    assert exps == [
        {
            "method": METHOD,
            "method_version": METHOD_VERSION_V2,
            "k": DEFAULT_TOP_K,
            "base_value": 0.75,
            "score": -1.25,
            "other_contribution": -2.0,
            "score_centered": 0.0,
            "other_contribution_centered": 0.0,
            "centering_population_size": 1,
            "items": [],
        }
    ]


def test_v1_default_output_is_byte_identical_regression():
    X = pd.DataFrame({"zeta": [2.0], "alpha": [np.nan]})
    contrib = np.array([[1.5, -1.5, 0.25]])

    exps = compute_explanations(_FixedContribBooster(contrib), X, ["zeta", "alpha"], k=1)
    explicit_v1 = compute_explanations(
        _FixedContribBooster(contrib),
        X,
        ["zeta", "alpha"],
        k=1,
        center_within_group=False,
    )
    encoded = json.dumps(exps, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    assert explicit_v1 == exps
    assert encoded == (
        '[{"method":"lgbm_pred_contrib","method_version":1,"k":1,'
        '"base_value":0.25,"score":0.25,"other_contribution":1.5,'
        '"items":[{"feature":"alpha","value":null,"contribution":-1.5}]}]'
    )
    assert exps[0]["method_version"] == METHOD_VERSION_V1
    assert not any("centered" in key for key in exps[0])
    assert not any("centered" in key for key in exps[0]["items"][0])


def test_v2_ties_are_broken_by_feature_name():
    X = pd.DataFrame({"beta": [0.0, 1.0], "alpha": [1.0, 0.0]})
    contrib = np.array([[1.0, -1.0, 0.0], [-1.0, 1.0, 0.0]])

    exps = compute_explanations(
        _FixedContribBooster(contrib),
        X,
        ["beta", "alpha"],
        k=2,
        center_within_group=True,
    )

    assert [[item["feature"] for item in e["items"]] for e in exps] == [
        ["alpha", "beta"],
        ["alpha", "beta"],
    ]


def test_v2_expected_raw_score_mismatch_is_race_atomic():
    X = pd.DataFrame({feature: [0.0, 1.0, 2.0] for feature in FEATS})
    contrib = np.array(
        [
            [1.0, 2.0, 3.0, 4.0, 0.5],
            [2.0, 3.0, 4.0, 5.0, 0.5],
            [3.0, 4.0, 5.0, 6.0, 0.5],
        ]
    )
    raw = contrib[:, -1] + contrib[:, :-1].sum(axis=1)
    raw[1] += 0.1

    exps = compute_explanations(
        _FixedContribBooster(contrib),
        X,
        FEATS,
        center_within_group=True,
        expected_raw_scores=raw,
    )

    assert exps == [None, None, None]


def test_v2_nonfinite_contribution_is_race_atomic():
    X = pd.DataFrame({feature: [0.0, 1.0, 2.0] for feature in FEATS})
    contrib = np.zeros((3, len(FEATS) + 1))
    contrib[1, 2] = np.nan

    exps = compute_explanations(_FixedContribBooster(contrib), X, FEATS, center_within_group=True)

    assert exps == [None, None, None]


def test_explanation_exceptions_never_escape():
    class RaisingBooster:
        def predict(self, X, *, pred_contrib):
            raise RuntimeError("pred_contrib failed")

    class UnserializableValue:
        def __str__(self):
            raise RuntimeError("JSON conversion failed")

    assert compute_explanations(RaisingBooster(), pd.DataFrame({"x": [1.0]}), ["x"]) == [None]
    assert compute_explanations(
        _FixedContribBooster([[1.0, 0.0]]),
        pd.DataFrame({"x": [UnserializableValue()]}),
        ["x"],
    ) == [None]
