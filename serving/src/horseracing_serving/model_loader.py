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
from horseracing_features.registry import (
    FEATURE_VERSION,
    is_feature_version_servable,
    model_input_features,
)
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
    objective: str = "binary"  # 039/042: "binary" | "cond_logit" | "pl_topk"
    metadata: dict = field(default_factory=dict)
    #: Feature 060: market-offset definition dict (metadata.market_offset) for models whose
    #: race-softmax score is log-q + trees. None for every ordinary model (path unchanged).
    market_offset: dict | None = None

    def raw_predict(self, X: pd.DataFrame, offsets: np.ndarray | None = None) -> np.ndarray:
        """Per-horse win score. binary: booster P(win). cond_logit: race-softmax.

        Feature 039: predict_race passes ONE race's started horses, so a cond_logit
        booster's raw margins are softmaxed over the whole batch (= that race).

        Feature 060: a market-offset model REQUIRES row-aligned ``offsets`` (log-q of the
        target race's own odds) added to the raw margin BEFORE the softmax — the booster
        stores only the residual trees. Mismatches fail closed in both directions.
        """
        if self.market_offset is not None and offsets is None:
            raise ServingError(
                f"model '{self.model_version}' is market-offset; raw_predict requires offsets"
            )
        if self.market_offset is None and offsets is not None:
            raise ServingError("offsets passed to a non-market-offset model")
        if self.booster is None:
            return np.full(len(X), self.degenerate_constant, dtype=float)
        raw = np.asarray(self.booster.predict(X[self.feature_cols]), dtype=float)
        if offsets is not None:
            off = np.asarray(offsets, dtype=float)
            if len(off) != len(raw) or not np.isfinite(off).all():
                raise ServingError("invalid market offsets (length/finite check failed)")
            raw = raw + off
        if self.objective in ("cond_logit", "pl_topk"):  # same race-softmax postprocess
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


def _load_preprocessor(art_dir: Path, metadata: dict, model_hash: str, exact: bool) -> dict:
    """Resolve the model's own feature schema and verify it is buildable + self-consistent.

    ``model_hash`` is the model's OWN trained feature_hash (metadata.feature_hash), NOT the
    current global hash. On the compatibility path (``exact=False``) the model was trained on a
    subset/older feature version; its columns must (a) hash to their stored value (integrity)
    and (b) be a subset of the current buildable columns (buildability) — else fail-closed.
    """
    current_cols = model_input_features()
    prep_path = art_dir / "preprocessor.pkl"
    if prep_path.exists():
        with prep_path.open("rb") as fh:
            prep = pickle.load(fh)
        if prep.get("feature_hash") != model_hash:
            raise ServingError("preprocessor feature_hash mismatch vs metadata")
        prep_cols = list(prep["feature_cols"])
        # integrity: the stored hash must actually be the hash of the stored columns.
        if feature_hash(prep_cols) != model_hash:
            raise ServingError("preprocessor feature_cols do not match stored feature_hash")
        # buildability: every column the model needs must exist in the current registry.
        missing = [c for c in prep_cols if c not in current_cols]
        if missing:
            raise ServingError(
                f"model feature_cols not buildable under current registry: {missing}"
            )
        # consistency: categorical/encoder columns must be within the declared feature set, else
        # predict_race()/raw_predict() would KeyError at inference instead of failing closed here.
        col_set = set(prep_cols)
        bad_cat = [c for c in prep.get("categorical_cols", []) if c not in col_set]
        if bad_cat:
            raise ServingError(f"categorical_cols not in feature_cols: {bad_cat}")
        bad_enc = [c for c in prep.get("encoders", {}) if c not in col_set]
        if bad_enc:
            raise ServingError(f"encoder columns not in feature_cols: {bad_enc}")
        # a TE model whose preprocessor carries NO fitted encoders would silently skip encoding
        # at inference -> fail closed instead (codex review).
        if metadata.get("target_encode_cols") and not prep.get("encoders"):
            raise ServingError(
                "metadata declares target_encode_cols but preprocessor has no encoders"
            )
        return prep
    # backward-compat: no preprocessor artifact. Only the exact-version path is supported here
    # (a subset/older model without a preprocessor cannot declare its own columns).
    if not exact:
        raise ServingError(
            "compat-version model has no preprocessor.pkl to declare its feature_cols; re-save"
        )
    if metadata.get("target_encode_cols"):
        raise ServingError(
            "model used target encoding but preprocessor.pkl is missing; re-save the model"
        )
    if feature_hash(current_cols) != model_hash:
        raise ServingError("feature_hash mismatch: trained schema differs from current features")
    return {
        "feature_cols": current_cols,
        "categorical_cols": [c for c in CATEGORICAL_FEATURES if c in current_cols],
        "target_encode_cols": [],
        "te_smoothing": DEFAULT_SMOOTHING,
        "encoders": {},
        "feature_hash": model_hash,
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

    # INV-S4: the trained feature schema must be servable under the current registry.
    # Fast path: the trained hash IS the current global hash (behaviour byte-identical to pre-058).
    # Compat path (Feature 058, 案C'): an OLDER version whose columns are an additive subset of the
    # current registry may serve by selecting its own columns — allowed only for a parity-tested
    # transition (is_feature_version_servable) with subset+integrity enforced in _load_preprocessor.
    current_hash = feature_hash(model_input_features())
    model_hash = metadata.get("feature_hash")
    model_fv = metadata.get("feature_version", "")
    exact = model_hash == current_hash
    if not exact and not is_feature_version_servable(model_fv, model_hash):
        raise ServingError(
            f"feature_hash mismatch for '{mv_name}': trained {model_fv!r} not servable under "
            f"current {FEATURE_VERSION!r} (no parity-tested compatibility for this hash)"
        )

    prep = _load_preprocessor(art_dir, metadata, model_hash, exact)

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

    # Feature 060: market-offset models carry their offset definition in metadata (and the
    # preprocessor, when present — a disagreement between the two is a broken artifact).
    market_offset = metadata.get("market_offset")
    prep_mo = prep.get("market_offset")
    if (market_offset is None) != (prep_mo is None) or (
        market_offset is not None and prep_mo is not None
        and market_offset.get("kind") != prep_mo.get("kind")
    ):
        raise ServingError(
            f"market_offset mismatch between metadata and preprocessor for '{mv_name}'"
        )
    if market_offset is not None and degenerate:
        raise ServingError(f"market-offset model '{mv_name}' is degenerate (no booster)")
    if market_offset is not None and market_offset.get("kind") != "log_q_devig":
        raise ServingError(
            f"unsupported market_offset kind {market_offset.get('kind')!r} for '{mv_name}'"
        )

    return ServingModel(
        model_version=mv_name,
        booster=booster,
        degenerate_constant=constant,
        calibrator=calibrator,
        feature_cols=list(prep["feature_cols"]),
        categorical_cols=list(prep["categorical_cols"]),
        encoders=dict(prep.get("encoders", {})),
        feature_version=metadata.get("feature_version", ""),
        feature_hash=model_hash,
        # Feature 039: prefer preprocessor, fall back to metadata, default binary (pre-039)
        objective=prep.get("objective", metadata.get("objective", "binary")),
        metadata=metadata,
        market_offset=market_offset,
    )
