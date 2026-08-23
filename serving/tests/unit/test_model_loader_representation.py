"""Feature 098: artifact representation and categorical-vocabulary binding."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import lightgbm as lgb
import pandas as pd
import pytest
from horseracing_training.artifacts import (
    categorical_vocab_from_booster,
    feature_hash,
    vocab_hash,
)
from horseracing_training.calibration import Calibrator

from horseracing_serving import model_loader
from horseracing_serving.model_loader import (
    ServingError,
    load_serving_model,
    resolve_representation,
)

_FEATURE_COLS = ["race_class", "age"]
_FEATURE_HASH = feature_hash(_FEATURE_COLS)
_ABSENT = object()


class _ModelVersion:
    def __init__(self, art_dir: Path) -> None:
        self.weights_uri = str(art_dir / "model.txt")
        self.calibrator_uri = str(art_dir / "calibrator.pkl")


class _Session:
    def __init__(self, model_version: _ModelVersion) -> None:
        self.model_version = model_version

    def get(self, _model, _name):
        return self.model_version


@pytest.fixture(autouse=True)
def _registry_features023(monkeypatch):
    """Keep these tests independent of stream A's registry edit timing."""
    monkeypatch.setattr(model_loader, "FEATURE_VERSION", "features-023")
    monkeypatch.setattr(model_loader, "RACE_CLASS_REPRESENTATION", "canonical-v1")
    monkeypatch.setattr(
        model_loader,
        "COMPATIBLE_PRIOR_FEATURE_VERSIONS",
        {"features-023": {"features-021": _FEATURE_HASH}},
    )
    monkeypatch.setattr(model_loader, "model_input_features", lambda: list(_FEATURE_COLS))
    monkeypatch.setattr(model_loader, "resolve_model_version", lambda *_a, **_k: "fixture")


def _write_artifact(
    tmp_path: Path,
    *,
    trained_fv: str,
    categories: list[str],
    marker: object = _ABSENT,
    include_vocab_hash: bool = True,
    vocab_hash_override: str | None = None,
) -> _Session:
    art_dir = tmp_path / trained_fv
    art_dir.mkdir()
    values = [categories[i % len(categories)] for i in range(8)]
    frame = pd.DataFrame(
        {
            "race_class": pd.Categorical(values, categories=categories),
            "age": [2, 3, 4, 5, 2, 3, 4, 5],
        }
    )
    booster = lgb.train(
        {
            "objective": "binary",
            "verbosity": -1,
            "min_data_in_leaf": 1,
            "min_data_in_bin": 1,
            "num_leaves": 3,
            "seed": 42,
        },
        lgb.Dataset(frame, label=[0, 1, 0, 1, 0, 1, 0, 1]),
        num_boost_round=2,
    )
    booster.save_model(str(art_dir / "model.txt"))
    with (art_dir / "calibrator.pkl").open("wb") as fh:
        pickle.dump(Calibrator(method="identity", identity=True), fh)
    with (art_dir / "preprocessor.pkl").open("wb") as fh:
        pickle.dump(
            {
                "feature_cols": list(_FEATURE_COLS),
                "categorical_cols": ["race_class"],
                "target_encode_cols": [],
                "encoders": {},
                "feature_hash": _FEATURE_HASH,
                "objective": "binary",
            },
            fh,
        )

    vocab = categorical_vocab_from_booster(booster, _FEATURE_COLS, ["race_class"])
    metadata = {
        "feature_version": trained_fv,
        "feature_hash": _FEATURE_HASH,
        "model_degenerate": False,
        "categorical_vocab": vocab,
    }
    if marker is not _ABSENT:
        metadata["race_class_representation"] = marker
    if include_vocab_hash:
        metadata["categorical_vocab_hash"] = vocab_hash_override or vocab_hash(vocab)
    (art_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False))
    return _Session(_ModelVersion(art_dir))


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("features-023", "features-023", True, "canonical-v1", "canonical-v1"),
         "canonical-v1"),
        (("features-021", "features-023", False, None, "canonical-v1"), "raw"),
        (("features-021", "features-023", False, "raw", "canonical-v1"), "raw"),
        (("features-021", "features-023", False, "canonical-v1", "canonical-v1"), "raw"),
        (("features-018", "features-021", False, None, None), "raw"),
        (("features-021", "features-021", True, None, None), "raw"),
        (("features-021", "features-021", True, "raw", "raw"), "raw"),
        (("features-021", "features-021", True, "canonical-v1", "canonical-v1"),
         "canonical-v1"),
    ],
)
def test_resolve_representation_allowlist(args, expected):
    assert resolve_representation(*args) == expected


@pytest.mark.parametrize(
    "args",
    [
        ("features-023", "features-023", True, None, "canonical-v1"),
        ("features-023", "features-023", True, "raw", "canonical-v1"),
        ("features-023", "features-023", True, "canonical-v2", "canonical-v1"),
        ("features-020", "features-023", False, None, "canonical-v1"),
        ("features-021", "features-023", True, "canonical-v1", "canonical-v1"),
        ("features-021", "features-021", True, "canonical-v1", "raw"),
        ("features-018", "features-021", False, "future-representation", None),
    ],
)
def test_resolve_representation_rejects_undeclared_or_inconsistent_paths(args):
    with pytest.raises(ServingError):
        resolve_representation(*args)


def test_raw_021_golden_fixture_loads_without_marker_or_vocab_hash(tmp_path):
    session = _write_artifact(
        tmp_path,
        trained_fv="features-021",
        categories=["１勝", "２勝"],
        include_vocab_hash=False,
    )

    model = load_serving_model(session)

    assert model.race_class_representation == "raw"
    assert model.categorical_vocab["race_class"] == ["１勝", "２勝"]


def test_canonical_023_golden_fixture_loads_with_bound_vocab(tmp_path):
    session = _write_artifact(
        tmp_path,
        trained_fv="features-023",
        categories=["1勝", "2勝", "3勝"],
        marker="canonical-v1",
    )

    model = load_serving_model(session)

    assert model.race_class_representation == "canonical-v1"
    assert model.categorical_vocab["race_class"] == ["1勝", "2勝", "3勝"]


@pytest.mark.parametrize(
    ("trained_fv", "marker", "categories", "include_hash", "hash_override"),
    [
        ("features-023", _ABSENT, ["1勝"], True, None),
        ("features-023", "raw", ["1勝"], True, None),
        ("features-023", "canonical-v1", ["1勝"], True, "deadbeef"),
        ("features-023", "canonical-v1", ["１勝", "1勝"], True, None),
        ("features-020", _ABSENT, ["1勝"], False, None),
    ],
)
def test_invalid_representation_artifacts_fail_closed(
    tmp_path, trained_fv, marker, categories, include_hash, hash_override
):
    session = _write_artifact(
        tmp_path,
        trained_fv=trained_fv,
        categories=categories,
        marker=marker,
        include_vocab_hash=include_hash,
        vocab_hash_override=hash_override,
    )

    with pytest.raises(ServingError):
        load_serving_model(session)
