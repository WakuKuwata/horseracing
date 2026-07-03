"""US3 (SC-005): train-evaluate persists a model_versions row + artifacts, reloadable,
with full reproducibility metadata (seed/params/fold/calibration/feature hash/git sha)."""

from __future__ import annotations

import json
import pickle

import pytest
from horseracing_db.models import ModelVersion
from horseracing_eval.baselines import UniformBaseline
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from horseracing_eval.store import save_baseline

from horseracing_training.cli import train_evaluate
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def test_train_evaluate_saves_row_and_artifacts(session, tmp_path):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=12, field_size=8)
    races = load_eval_races(session)

    # baseline must exist for the adoption gate (same eval conditions)
    uniform = evaluate(UniformBaseline(), races, first_valid_year=2008)
    save_baseline(session, "uniform", uniform)

    summary = train_evaluate(
        session,
        first_valid_year=2008,
        calibration="platt",
        ece_threshold=0.5,
        baseline="uniform",
        model_version="lgbm-test",
        artifacts_dir=str(tmp_path),
        seed=42,
    )
    assert summary["overall"]["win"]["log_loss"] is not None

    mv = session.get(ModelVersion, "lgbm-test")
    assert mv is not None
    assert mv.model_family == "lightgbm"
    assert mv.label_schema == "win_top2_top3"
    assert mv.adoption_status in ("active", "candidate")
    assert mv.weights_uri and mv.calibrator_uri
    assert mv.metrics_summary["eval"]["overall"]["win"]["log_loss"] is not None
    assert mv.metrics_summary["training"]["model_family"] == "lightgbm"
    # Feature 050 (V): the training-data window is answerable from the DB row alone —
    # same values as the on-disk metadata.json (train_through/n_model_rows/n_calib_rows).
    tr = mv.metrics_summary["training"]
    for key in ("train_through", "n_model_rows", "n_calib_rows"):
        assert key in tr
    assert tr["train_through"] is not None and tr["n_model_rows"] > 0

    art = tmp_path / "model_versions" / "lgbm-test"
    assert (art / "model.txt").exists()
    assert (art / "calibrator.pkl").exists()

    meta = json.loads((art / "metadata.json").read_text())
    for key in (
        "seed", "params", "fold_boundaries", "calibration",
        "feature_version", "feature_hash", "git_sha",
    ):
        assert key in meta
    assert meta["fold_boundaries"] == summary["valid_years"]

    with (art / "calibrator.pkl").open("rb") as fh:
        calibrator = pickle.load(fh)
    assert hasattr(calibrator, "transform")
