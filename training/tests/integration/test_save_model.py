"""US3 (SC-005): train-evaluate persists a model_versions row + artifacts, reloadable,
with full reproducibility metadata (seed/params/fold/calibration/feature hash/git sha)."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

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
    # 2026-08: the legacy 4-metric gate alone can no longer activate a model. This call passes no
    # v3 verdict, so the row must land as a CANDIDATE with the reason recorded. The previous
    # assertion here was `in ("active", "candidate")` — a tautology that covered nothing.
    assert mv.adoption_status == "candidate"
    promo = mv.metrics_summary["promotion"]
    assert promo["promotable"] is False
    assert promo["reasons"]["cause"] in (
        "no_v3_verdict_supplied", "legacy_gate_not_adopted", "register_as_candidate_requested",
    )
    assert mv.weights_uri and mv.calibrator_uri
    # URIs must be ABSOLUTE so they resolve from any cwd — the ops predict job shells out to the
    # serving CLI with cwd=serving/, and a bare-relative URI would fail "metadata.json missing".
    assert Path(mv.weights_uri).is_absolute()
    assert Path(mv.calibrator_uri).is_absolute()
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


# --- drop_features で学習した artifact の刻印と昇格(2026-08-23) -------------------------------
#
# 実害の記録: `feature_version` は呼び出し元が渡すコード定数で、`drop_features` で列を減らして
# 学習しても現行版を名乗る。一方 `feature_hash` は実列由来。両者が食い違った artifact は
# serving の exact 経路にも compat pin にも乗らず**永久にロードできない**。
# lgbm-065 がまさにこれで、active になった直後に predict ジョブが 37/37 失敗した(2026-07)。
# 実 DB には今もこの行が残っている(feature_version=features-018 / hash=features-017 相当)。

def test_a_dropped_column_build_is_recorded_as_unservable_and_stays_candidate(session, tmp_path):
    from horseracing_features.registry import FEATURE_VERSION, model_input_features

    from horseracing_training.artifacts import feature_hash

    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=12, field_size=8)
    races = load_eval_races(session)
    save_baseline(session, "uniform", evaluate(UniformBaseline(), races, first_valid_year=2008))

    dropped = ("jockey_win_rate",)
    assert dropped[0] in model_input_features(), "前提: 落とす列が現行スキーマに存在すること"

    train_evaluate(
        session,
        first_valid_year=2008, calibration="platt", ece_threshold=0.5, baseline="uniform",
        model_version="lgbm-dropcols", artifacts_dir=str(tmp_path), seed=42,
        drop_features=dropped,
    )

    mv = session.get(ModelVersion, "lgbm-dropcols")
    fs = mv.metrics_summary["training"]["feature_schema"]

    # 刻印は現行版を名乗るが、実列はそれではない ← これが誤刻印の本体
    assert fs["feature_version"] == FEATURE_VERSION
    assert fs["feature_hash"] != feature_hash(model_input_features())
    assert fs["n_feature_cols"] == len(model_input_features()) - len(dropped)

    # その事実が記録として残り、serve できないことが明示されている
    assert fs["is_current_schema"] is False
    assert fs["servable"] is False
    assert "not_servable_reason" in fs

    # そして ACTIVE にはならない(2026-07 の停止事故の入口を閉じる)
    assert mv.adoption_status == "candidate"
    assert mv.metrics_summary["promotion"]["reasons"]["cause"] == "artifact_not_servable"

    # metadata.json 側にも同じ事実が載る(serving/運用が読むのはこちら)
    meta = json.loads((Path(mv.weights_uri).parent / "metadata.json").read_text())
    assert meta["feature_schema"]["servable"] is False


def test_a_full_schema_build_is_recorded_as_servable(session, tmp_path):
    """正常系が変わっていないこと(この検査が全部を candidate にしていないことの証明)."""
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=12, field_size=8)
    races = load_eval_races(session)
    save_baseline(session, "uniform", evaluate(UniformBaseline(), races, first_valid_year=2008))

    train_evaluate(
        session,
        first_valid_year=2008, calibration="platt", ece_threshold=0.5, baseline="uniform",
        model_version="lgbm-fullcols", artifacts_dir=str(tmp_path), seed=42,
    )
    fs = session.get(ModelVersion, "lgbm-fullcols").metrics_summary["training"]["feature_schema"]
    assert fs["is_current_schema"] is True
    assert fs["servable"] is True
    assert "not_servable_reason" not in fs
