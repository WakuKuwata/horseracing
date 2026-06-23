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
        wm.booster_.booster_.save_model(str(path))
    else:  # degenerate constant model — no LightGBM booster to serialize
        const = 0.0 if (wm is None or wm._constant is None) else wm._constant
        path.write_text(json.dumps({"degenerate_constant_win": const}))


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
    meta_path = art_dir / "metadata.json"

    # 1. artifacts to disk
    _write_model(predictor, model_path)
    with calib_path.open("wb") as fh:
        pickle.dump(predictor.calibrator_, fh)

    metadata = {
        "model_version": model_version,
        "model_family": MODEL_FAMILY,
        "seed": info.get("seed"),
        "params": info.get("params"),
        "calibration": info.get("calibration"),
        "calibrator_params": predictor.calibrator_.params_dict() if predictor.calibrator_ else None,
        "fold_boundaries": list(eval_result.valid_years),
        "feature_version": feature_version,
        "feature_hash": feature_hash(fcols),
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
    summary["training"] = {
        "model_family": MODEL_FAMILY,
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
