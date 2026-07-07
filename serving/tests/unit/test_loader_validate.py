"""US2 (SC-004): booster/predict_proba parity + preprocessor fail-fast (DB-free)."""

from __future__ import annotations

import pickle

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
from horseracing_features.registry import model_input_features
from horseracing_training.artifacts import feature_hash
from horseracing_training.win_model import WinModel

from horseracing_serving.model_loader import ServingError, _load_preprocessor


def test_booster_predict_matches_predict_proba(tmp_path):
    # Training raw = LGBMClassifier.predict_proba[:,1]; serving raw = lgb.Booster.predict.
    # They must agree (binary objective -> probability), or calibration is built on a
    # different scale than it is applied to.
    n = 80
    df = pd.DataFrame({
        "x": [float(i % 5) for i in range(n)],
        "g": pd.Categorical(["a" if i % 2 else "b" for i in range(n)]),
    })
    y = np.array([1 if (i % 5 == 0) else 0 for i in range(n)])
    wm = WinModel(seed=42).fit(df, y, categorical_cols=["g"])
    proba = wm.predict(df)

    path = tmp_path / "model.txt"
    wm.booster_.booster_.save_model(str(path))
    booster = lgb.Booster(model_file=str(path))
    raw = np.asarray(booster.predict(df[wm.feature_cols_]), dtype=float)

    assert np.max(np.abs(proba - raw)) < 1e-7


def test_te_model_missing_preprocessor_fails(tmp_path):
    meta = {"target_encode_cols": ["jockey_id"], "feature_hash": "abc"}
    with pytest.raises(ServingError):
        _load_preprocessor(tmp_path, meta, "abc", exact=True)  # no preprocessor.pkl present


def test_no_te_missing_preprocessor_reconstructs(tmp_path):
    h = feature_hash(model_input_features())
    prep = _load_preprocessor(
        tmp_path, {"target_encode_cols": [], "feature_hash": h}, h, exact=True
    )
    assert prep["feature_cols"] == model_input_features()
    assert prep["encoders"] == {}


def test_feature_hash_mismatch_fails(tmp_path):
    # no preprocessor + no TE, but the expected hash disagrees with current features
    with pytest.raises(ServingError):
        _load_preprocessor(
            tmp_path, {"target_encode_cols": [], "feature_hash": "wrong"}, "wrong", exact=True
        )


def test_preprocessor_pkl_hash_mismatch_fails(tmp_path):
    with (tmp_path / "preprocessor.pkl").open("wb") as fh:
        pickle.dump({"feature_hash": "a", "feature_cols": [], "categorical_cols": [],
                     "encoders": {}}, fh)
    with pytest.raises(ServingError):
        _load_preprocessor(tmp_path, {}, "b", exact=False)


# Feature 058 (案C'): compat-path — an older model whose feature_cols are a byte-parity-tested
# subset of the current registry loads by declaring its own columns via preprocessor.pkl.
def test_compat_subset_preprocessor_loads(tmp_path):
    subset = model_input_features()[:-1]  # simulate an older version = current minus 1 column
    h = feature_hash(subset)
    with (tmp_path / "preprocessor.pkl").open("wb") as fh:
        pickle.dump({"feature_hash": h, "feature_cols": subset, "categorical_cols": [],
                     "encoders": {}}, fh)
    prep = _load_preprocessor(tmp_path, {"feature_hash": h}, h, exact=False)
    assert prep["feature_cols"] == subset


def test_compat_unbuildable_column_fails(tmp_path):
    # a column the current registry cannot build -> fail-closed (buildability guard)
    cols = [*model_input_features(), "a_column_that_does_not_exist"]
    h = feature_hash(cols)
    with (tmp_path / "preprocessor.pkl").open("wb") as fh:
        pickle.dump({"feature_hash": h, "feature_cols": cols, "categorical_cols": [],
                     "encoders": {}}, fh)
    with pytest.raises(ServingError):
        _load_preprocessor(tmp_path, {"feature_hash": h}, h, exact=False)


def test_compat_integrity_cols_vs_hash_fails(tmp_path):
    # stored feature_hash matches metadata but is NOT the hash of the stored columns -> fail-closed
    subset = model_input_features()[:-1]
    with (tmp_path / "preprocessor.pkl").open("wb") as fh:
        pickle.dump({"feature_hash": "matches_meta", "feature_cols": subset,
                     "categorical_cols": [], "encoders": {}}, fh)
    with pytest.raises(ServingError):
        _load_preprocessor(tmp_path, {"feature_hash": "matches_meta"}, "matches_meta", exact=False)


def test_compat_model_without_preprocessor_fails(tmp_path):
    # a compat-version model with no preprocessor cannot declare its columns -> fail-closed
    with pytest.raises(ServingError):
        _load_preprocessor(tmp_path, {"feature_hash": "x"}, "x", exact=False)
