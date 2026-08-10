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
from horseracing_training.win_model import WinModel

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
    model: ServingModel, race_id: str, feature_rows: pd.DataFrame, *,
    stage_discount=None, win_odds: dict[str, float | None] | None = None,
) -> tuple[dict[str, Prediction], dict[str, dict], dict[str, dict | None]]:
    """Feature 060: for a market-offset model the caller supplies ``win_odds``
    (horse_id -> win odds of THIS race); the devig log-q offset is rebuilt here with the
    same pure functions as training (INV-M1). Missing/invalid odds for ANY started horse
    fails closed — no silent no-offset fallback (INV-M4)."""
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

    offsets = None
    if model.market_offset is not None:
        from horseracing_training.market_offset import log_q_offset, valid_odds_mask

        odds = np.asarray(
            [(win_odds or {}).get(hid) for hid in started_ids], dtype=float
        )
        if not valid_odds_mask(odds).all():
            raise ValueError(
                f"market-offset model: race {race_id} lacks full odds coverage (fail-closed)"
            )
        offsets = log_q_offset(odds)
    raw = model.raw_predict(X, offsets=offsets)
    calibrated = np.asarray(model.calibrator.transform(raw), dtype=float)
    # Feature 049: stage_discount (opt-in) corrects top2/top3 only; win is untouched (INV-S2).
    predictions = assemble_predictions(
        started_ids, calibrated, eps=DEFAULT_CLIP, stage_discount=stage_discount
    )

    # Feature 040/089: per-horse score-contribution explanation (display-only; NEVER a model
    # feature). Degenerate model (no booster) -> all None. Never touches predictions/snapshots
    # (INV-E2), and any failure degrades to "no explanation", never to a failed prediction.
    #
    # 089: for a race-softmax model WITHOUT a market offset the contributions are centred within
    # the race (v2) — race-constant contributions cancel in the softmax, so selecting the top-K by
    # the population-relative magnitude surfaces features that cannot affect the ordering (e.g.
    # every debutant sharing prev_finish=NaN). Excluded from v2:
    #   * binary objective — no race-softmax, so a race-constant contribution DOES move p_i;
    #   * market-offset models — the softmax input is (log-q offset + tree margin) but pred_contrib
    #     only decomposes the tree part, so "relative score decomposition" would be a false claim.
    # ``expected_raw_scores`` is the INDEPENDENT tree margin used to reconcile the decomposition
    # (base + Σcontrib), and it is supplied ONLY on the v2 path. It must NOT be ``raw``:
    # ``ServingModel.raw_predict`` post-processes race-softmax objectives (and adds the market
    # offset), so ``raw`` is a probability vector here, not a margin — reconciling against it fails
    # on every row and would wipe out every v2 explanation. Take the margin straight from the
    # booster instead (one extra forward pass over the ~16 started rows; the pred_contrib call
    # inside compute_explanations already costs more).
    if model.booster is not None:
        try:
            # Inside the guard on purpose: even the objective/offset probe must not be able to
            # raise into the prediction path (INV-E2).
            centered = (
                model.objective in WinModel.SOFTMAX_OBJECTIVES and model.market_offset is None
            )
            margin = (
                np.asarray(
                    model.booster.predict(X[model.feature_cols], raw_score=True), dtype=float
                )
                if centered
                else None
            )
            exp_list = compute_explanations(
                model.booster,
                X,
                model.feature_cols,
                center_within_group=centered,
                expected_raw_scores=margin,
            )
            explanations: dict[str, dict | None] = dict(
                zip(started_ids, exp_list, strict=True)
            )
        except Exception:  # noqa: BLE001 — explanation must never break the prediction pipeline
            explanations = {hid: None for hid in started_ids}
    else:
        explanations = {hid: None for hid in started_ids}

    snapshots: dict[str, dict] = {}
    for i, hid in enumerate(started_ids):
        feat = {c: _jsonable(X.iloc[i][c]) for c in model.feature_cols}
        feat["_raw_win"] = float(raw[i])
        feat["_calibrated_win"] = float(calibrated[i])
        snapshots[hid] = feat
    return predictions, snapshots, explanations
