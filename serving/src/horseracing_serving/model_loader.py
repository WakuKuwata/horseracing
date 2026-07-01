"""Resolve and load an adopted model for serving (contracts/serving.md, contracts/artifacts.md).

A ServingModel bundles everything needed to reproduce the training-time input matrix and
inference OUTSIDE the training session: the LightGBM booster (or a degenerate constant),
the calibrator, and the preprocessor (feature column order, native categorical columns,
fitted target encoders). The loader fails fast (no silent mismatch) when:
- the current feature schema hash disagrees with the trained feature_hash (INV-S4), or
- a target-encoded model is missing its preprocessor artifact (codex BLOCKER guard).
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion
from horseracing_features.registry import model_input_features
from horseracing_training.artifacts import feature_hash
from horseracing_training.dataset import CATEGORICAL_FEATURES
from horseracing_training.target_encoding import DEFAULT_SMOOTHING
from sqlalchemy import select
from sqlalchemy.orm import Session


class ServingError(RuntimeError):
    """Raised when no/ambiguous model, missing artifacts, or feature-schema mismatch."""


@dataclass(frozen=True)
class ServingModel:
    model_version: str
    booster: lgb.Booster | None       # None -> degenerate constant model
    degenerate_constant: float
    calibrator: object                # training.calibration.Calibrator
    feature_cols: list[str]
    categorical_cols: list[str]
    encoders: dict = field(default_factory=dict)  # col -> TargetEncoder
    feature_version: str = ""
    feature_hash: str = ""
    objective: str = "binary"  # Feature 039: "binary" | "cond_logit"
    metadata: dict = field(default_factory=dict)

    def raw_predict(self, X: pd.DataFrame) -> np.ndarray:
        """Per-horse win score. binary: booster P(win). cond_logit: race-softmax.

        Feature 039: predict_race passes ONE race's started horses, so a cond_logit
        booster's raw margins are softmaxed over the whole batch (= that race).
        """
        if self.booster is None:
            return np.full(len(X), self.degenerate_constant, dtype=float)
        raw = np.asarray(self.booster.predict(X[self.feature_cols]), dtype=float)
        if self.objective == "cond_logit":
            from horseracing_training.cond_logit import race_softmax

            return race_softmax(raw, [len(raw)]) if len(raw) else raw
        return raw


def resolve_model_version(session: Session, explicit: str | None = None) -> str:
    if explicit is not None:
        if session.get(ModelVersion, explicit) is None:
            raise ServingError(f"model_version '{explicit}' not found")
        return explicit
    actives = session.scalars(
        select(ModelVersion).where(ModelVersion.adoption_status == AdoptionStatus.ACTIVE)
    ).all()
    if not actives:
        raise ServingError("no active model_version; pass --model-version explicitly")
    if len(actives) > 1:
        names = ", ".join(m.model_version for m in actives)
        raise ServingError(f"multiple active models ({names}); pass --model-version explicitly")
    return actives[0].model_version


def _load_preprocessor(art_dir: Path, metadata: dict, expected_hash: str) -> dict:
    prep_path = art_dir / "preprocessor.pkl"
    if prep_path.exists():
        with prep_path.open("rb") as fh:
            prep = pickle.load(fh)
        if prep.get("feature_hash") != expected_hash:
            raise ServingError("preprocessor feature_hash mismatch vs metadata")
        return prep
    # backward-compat: no preprocessor artifact
    if metadata.get("target_encode_cols"):
        raise ServingError(
            "model used target encoding but preprocessor.pkl is missing; re-save the model"
        )
    feature_cols = model_input_features()
    if feature_hash(feature_cols) != expected_hash:
        raise ServingError("feature_hash mismatch: trained schema differs from current features")
    return {
        "feature_cols": feature_cols,
        "categorical_cols": [c for c in CATEGORICAL_FEATURES if c in feature_cols],
        "target_encode_cols": [],
        "te_smoothing": DEFAULT_SMOOTHING,
        "encoders": {},
        "feature_hash": expected_hash,
    }


def load_serving_model(
    session: Session, model_version: str | None = None
) -> ServingModel:
    mv_name = resolve_model_version(session, model_version)
    mv = session.get(ModelVersion, mv_name)
    if mv is None or not mv.weights_uri or not mv.calibrator_uri:
        raise ServingError(f"model '{mv_name}' has no artifacts (weights/calibrator)")

    art_dir = Path(mv.weights_uri).parent
    meta_path = art_dir / "metadata.json"
    if not meta_path.exists():
        raise ServingError(f"metadata.json missing for '{mv_name}'")
    metadata = json.loads(meta_path.read_text())

    # INV-S4: current feature schema must match the trained one
    current_hash = feature_hash(model_input_features())
    if metadata.get("feature_hash") != current_hash:
        raise ServingError(
            f"feature_hash mismatch for '{mv_name}': trained != current model_input_features()"
        )

    prep = _load_preprocessor(art_dir, metadata, current_hash)

    # booster vs degenerate constant (training writes JSON when no booster)
    degenerate = bool(metadata.get("model_degenerate"))
    booster: lgb.Booster | None = None
    constant = 0.0
    if degenerate:
        constant = float(json.loads(Path(mv.weights_uri).read_text())["degenerate_constant_win"])
    else:
        booster = lgb.Booster(model_file=str(mv.weights_uri))

    with Path(mv.calibrator_uri).open("rb") as fh:
        calibrator = pickle.load(fh)

    return ServingModel(
        model_version=mv_name,
        booster=booster,
        degenerate_constant=constant,
        calibrator=calibrator,
        feature_cols=list(prep["feature_cols"]),
        categorical_cols=list(prep["categorical_cols"]),
        encoders=dict(prep.get("encoders", {})),
        feature_version=metadata.get("feature_version", ""),
        feature_hash=current_hash,
        # Feature 039: prefer preprocessor, fall back to metadata, default binary (pre-039)
        objective=prep.get("objective", metadata.get("objective", "binary")),
        metadata=metadata,
    )
