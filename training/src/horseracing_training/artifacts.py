"""Persist a trained predictor: file artifacts + model_versions row (R7, contracts/adoption.md).

No schema change: writes ``model.txt`` / ``calibrator.pkl`` / ``metadata.json`` under
``artifacts/model_versions/{model_version}/`` and upserts the existing ``model_versions``
row (metrics_summary + weights_uri + calibrator_uri). Files are written and the metadata is
assembled BEFORE the DB upsert (filesystem and DB are not one transaction — codex point).

The saved predictor is the *serving* model (trained on the full available history); the
walk-forward fold boundaries that produced ``eval_result`` are recorded in metadata so the
reported metrics stay reproducible/auditable (R7 per-fold-vs-final ambiguity resolved here).
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict
from pathlib import Path

from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion
from horseracing_eval.harness import EvalResult
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .adoption import AdoptionDecision, AdoptionGate
from .predictor import LightGBMPredictor

MODEL_FAMILY = "lightgbm"
LABEL_SCHEMA = "win_top2_top3"


def feature_hash(feature_cols: list[str]) -> str:
    return hashlib.sha256("|".join(feature_cols).encode()).hexdigest()


def _write_model(predictor: LightGBMPredictor, path: Path) -> None:
    wm = predictor.win_model_
    if wm is not None and wm.booster_ is not None:
        # binary -> LGBMClassifier (.booster_); cond_logit -> raw lgb.Booster (Feature 039)
        booster = getattr(wm.booster_, "booster_", wm.booster_)
        booster.save_model(str(path))
    else:  # degenerate constant model — no LightGBM booster to serialize
        const = 0.0 if (wm is None or wm._constant is None) else wm._constant
        path.write_text(json.dumps({"degenerate_constant_win": const}))


def build_preprocessor(predictor: LightGBMPredictor, feature_version: str) -> dict:
    """Serving-side preprocessing state (Feature 006): everything needed to rebuild the
    exact model-input matrix outside the training session — feature column order, native
    categorical columns, and the fitted target encoders. Stored as a plain dict (unpickled
    by ``horseracing_serving`` which path-depends on training, so TargetEncoder resolves)."""
    info = predictor.fit_info_ or {}
    fcols = info.get("feature_cols", predictor.feature_cols_ or [])
    return {
        "feature_cols": list(fcols),
        "categorical_cols": list(info.get("categorical_cols", [])),
        "target_encode_cols": list(predictor.te_cols_),
        "te_smoothing": predictor.te_smoothing,
        "encoders": dict(predictor.encoders_),  # col -> TargetEncoder (empty if no TE)
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "model_degenerate": bool(info.get("model_degenerate")),
        # Feature 039: serving must apply the matching postprocess (binary sigmoid vs
        # cond_logit race-softmax). Default "binary" keeps pre-039 artifacts backward-compatible.
        "objective": info.get("objective", "binary"),
        "postprocess": info.get("postprocess", "sigmoid"),
    }


def save_model_version(
    session: Session,
    *,
    model_version: str,
    predictor: LightGBMPredictor,
    eval_result: EvalResult,
    decision: AdoptionDecision,
    gate: AdoptionGate,
    artifacts_root: Path | str,
    feature_version: str,
    git_sha: str | None = None,
) -> Path:
    """Write artifacts and upsert the model_versions row. Returns the artifacts dir."""
    info = predictor.fit_info_ or {}
    fcols = info.get("feature_cols", predictor.feature_cols_ or [])

    root = Path(artifacts_root)
    art_dir = root / "model_versions" / model_version
    art_dir.mkdir(parents=True, exist_ok=True)
    model_path = art_dir / "model.txt"
    calib_path = art_dir / "calibrator.pkl"
    prep_path = art_dir / "preprocessor.pkl"
    meta_path = art_dir / "metadata.json"

    # 1. artifacts to disk
    _write_model(predictor, model_path)
    with calib_path.open("wb") as fh:
        pickle.dump(predictor.calibrator_, fh)
    with prep_path.open("wb") as fh:  # Feature 006 serving: preprocessing state
        pickle.dump(build_preprocessor(predictor, feature_version), fh)

    metadata = {
        "model_version": model_version,
        "model_family": MODEL_FAMILY,
        "objective": info.get("objective", "binary"),  # Feature 039
        "postprocess": info.get("postprocess", "sigmoid"),
        "seed": info.get("seed"),
        "params": info.get("params"),
        "calibration": info.get("calibration"),
        "calibrator_params": predictor.calibrator_.params_dict() if predictor.calibrator_ else None,
        "fold_boundaries": list(eval_result.valid_years),
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "target_encode_cols": list(predictor.te_cols_),  # serving backward-compat detection
        "te_smoothing": predictor.te_smoothing if predictor.te_cols_ else None,
        "git_sha": git_sha,
        "train_through": info.get("train_through"),
        "n_model_rows": info.get("n_model_rows"),
        "n_calib_rows": info.get("n_calib_rows"),
        "model_degenerate": info.get("model_degenerate"),
        "calibrator_degenerate": info.get("calibrator_degenerate"),
        "adoption": {"adopted": decision.adopted, **asdict(gate), "reasons": decision.reasons},
    }
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str))

    # 2. metrics_summary (eval shape + training meta) -> DB
    summary = eval_result.to_summary()
    # Feature 040 US2: split-gain feature importance for display (/models/{mv}/importance).
    # Absent (key omitted) for degenerate models -> API returns typed 404 importance_unavailable.
    if predictor.win_model_ is not None:
        gain = predictor.win_model_.gain_importance()
        if gain is not None:
            summary["importance"] = {"type": "gain", "values": gain}
    summary["training"] = {
        "model_family": MODEL_FAMILY,
        "objective": info.get("objective", "binary"),  # Feature 039
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
        "seed": info.get("seed"),
        "calibration": info.get("calibration"),
        "git_sha": git_sha,
        "adoption": metadata["adoption"],
    }

    status = AdoptionStatus.ACTIVE if decision.adopted else AdoptionStatus.CANDIDATE
    values = dict(
        model_version=model_version,
        model_family=MODEL_FAMILY,
        feature_version=feature_version,
        label_schema=LABEL_SCHEMA,
        adoption_status=str(status),
        metrics_summary=summary,
        weights_uri=str(model_path),
        calibrator_uri=str(calib_path),
    )
    stmt = insert(ModelVersion).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["model_version"],
        set_={k: values[k] for k in values if k != "model_version"},
    )
    session.execute(stmt)
    session.commit()
    return art_dir
