from __future__ import annotations

import json
import pickle

import lightgbm as lgb
import pandas as pd
import pytest

from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import (
    build_preprocessor,
    categorical_vocab_from_booster,
    save_model_version,
    vocab_hash,
)
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.win_model import WinModel


@pytest.fixture(scope="module")
def tiny_booster():
    categories = ["3勝", "1勝", "2勝"]
    frame = pd.DataFrame(
        {
            "numeric": [float(i) for i in range(20)],
            "race_class": pd.Categorical(
                [categories[i % len(categories)] for i in range(20)],
                categories=categories,
            ),
        }
    )
    labels = [i % 2 for i in range(20)]
    classifier = lgb.LGBMClassifier(
        n_estimators=5,
        min_child_samples=1,
        random_state=42,
        deterministic=True,
        num_threads=1,
        force_row_wise=True,
        verbose=-1,
    )
    classifier.fit(frame, labels, categorical_feature=["race_class"])
    return classifier


def test_vocab_rekeys_lightgbm_categories_and_hash_preserves_order(tiny_booster):
    vocab = categorical_vocab_from_booster(
        tiny_booster.booster_,
        ["numeric", "race_class"],
        ["race_class"],
    )

    assert vocab == {"race_class": ["3勝", "1勝", "2勝"]}
    assert vocab_hash(vocab) == vocab_hash({"race_class": ["3勝", "1勝", "2勝"]})
    assert vocab_hash(vocab) != vocab_hash({"race_class": ["1勝", "3勝", "2勝"]})


class _StubEval:
    valid_years: list[int] = []

    def to_summary(self) -> dict:
        return {"eval": {"overall": {}}}


class _FakeSession:
    def __init__(self):
        self.params: dict | None = None
        self.committed = False

    def get(self, _model, _key):
        return None

    def execute(self, statement):
        self.params = statement.compile().params

    def commit(self):
        self.committed = True


def test_save_model_version_writes_representation_and_vocab_metadata(tmp_path, tiny_booster):
    feature_cols = ["numeric", "race_class"]
    categorical_cols = ["race_class"]
    predictor = LightGBMPredictor(session=None)
    predictor.feature_cols_ = feature_cols
    predictor.win_model_ = WinModel(
        booster_=tiny_booster,
        feature_cols_=feature_cols,
    )
    predictor.fit_info_ = {
        "feature_cols": feature_cols,
        "categorical_cols": categorical_cols,
        "race_class_representation": "canonical-v1",
        "model_degenerate": False,
    }
    session = _FakeSession()

    art_dir = save_model_version(
        session,
        model_version="lgbm-vocab-test",
        predictor=predictor,
        eval_result=_StubEval(),
        decision=AdoptionDecision(adopted=False, reasons={}),
        gate=AdoptionGate(ece_threshold=0.1),
        artifacts_root=tmp_path,
        feature_version="features-023",
    )

    expected_vocab = {"race_class": ["3勝", "1勝", "2勝"]}
    metadata = json.loads((art_dir / "metadata.json").read_text())
    assert metadata["race_class_representation"] == "canonical-v1"
    assert metadata["categorical_vocab"] == expected_vocab
    assert metadata["categorical_vocab_hash"] == vocab_hash(expected_vocab)

    with (art_dir / "preprocessor.pkl").open("rb") as fh:
        preprocessor = pickle.load(fh)
    assert preprocessor["race_class_representation"] == "canonical-v1"
    assert build_preprocessor(predictor, "features-023")["race_class_representation"] == (
        "canonical-v1"
    )

    assert session.params is not None
    assert session.params["metrics_summary"]["training"]["race_class_representation"] == (
        "canonical-v1"
    )
    assert session.committed
