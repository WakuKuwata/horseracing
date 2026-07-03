"""Serving inference for one race (contracts/serving.md, INV-S1..S3).

Order: started-align -> apply target encoders -> booster raw -> calibrate -> clip ->
race-normalize -> Harville (all reused from training's pure parts). Returns a Prediction per
started horse plus a per-horse snapshot of the POST-preprocessing model-input vector (+ raw
and calibrated win) so the inference is fully reproducible/auditable even for TE models.
Session-independent: the caller supplies the as-of feature rows.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from horseracing_eval.predictor import Prediction
from horseracing_training.calibration import DEFAULT_CLIP
from horseracing_training.explanation import compute_explanations
from horseracing_training.predictor import assemble_predictions
from horseracing_training.target_encoding import apply_encoded_columns

from .model_loader import ServingModel


def _jsonable(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, float):
        return v
    if pd.isna(v):
        return None
    return str(v)


def predict_race(
    model: ServingModel, race_id: str, feature_rows: pd.DataFrame, *, stage_discount=None
) -> tuple[dict[str, Prediction], dict[str, dict], dict[str, dict | None]]:
    rows = feature_rows[feature_rows["race_id"] == race_id].copy()
    if rows.empty:
        raise ValueError(f"no started horses for race {race_id}")
    rows = rows.set_index("horse_id")
    # deterministic, stable horse order (float ops in Harville are order-sensitive)
    started_ids = sorted(rows.index.tolist())
    rows = rows.reindex(started_ids)

    # Match training's dtype coercion (build_feature_matrix leaves raw object/Decimal columns,
    # but the booster was trained on category + numeric like build_training_matrix produces).
    for col in model.categorical_cols:
        if col in rows.columns:
            rows[col] = rows[col].astype("category")
    numeric_cols = [
        c for c in model.feature_cols if c not in model.categorical_cols and c not in model.encoders
    ]
    for col in numeric_cols:
        rows[col] = pd.to_numeric(rows[col], errors="coerce")

    base = rows[model.feature_cols].copy()
    if model.encoders:  # target-encoded columns -> float
        encoded = {col: enc.transform(rows[col]) for col, enc in model.encoders.items()}
        X = apply_encoded_columns(base, encoded, model.feature_cols)
    else:
        X = base

    raw = model.raw_predict(X)
    calibrated = np.asarray(model.calibrator.transform(raw), dtype=float)
    # Feature 049: stage_discount (opt-in) corrects top2/top3 only; win is untouched (INV-S2).
    predictions = assemble_predictions(
        started_ids, calibrated, eps=DEFAULT_CLIP, stage_discount=stage_discount
    )

    # Feature 040: per-horse score-contribution explanation (display-only; NEVER a model feature).
    # Decomposes the RAW booster margin (before race-softmax/isotonic/009) — additive, top-K.
    # Degenerate model (no booster) -> all None. Does not touch predictions/snapshots (INV-E2).
    if model.booster is not None:
        exp_list = compute_explanations(model.booster, X, model.feature_cols)
        explanations: dict[str, dict | None] = dict(zip(started_ids, exp_list, strict=True))
    else:
        explanations = {hid: None for hid in started_ids}

    snapshots: dict[str, dict] = {}
    for i, hid in enumerate(started_ids):
        feat = {c: _jsonable(X.iloc[i][c]) for c in model.feature_cols}
        feat["_raw_win"] = float(raw[i])
        feat["_calibrated_win"] = float(calibrated[i])
        snapshots[hid] = feat
    return predictions, snapshots, explanations
