"""Serving inference for one race (contracts/serving.md, INV-S1..S3).

Order: started-align -> apply target encoders -> booster raw -> calibrate -> clip ->
race-normalize -> Harville (all reused from training's pure parts). Returns a Prediction per
started horse plus a per-horse snapshot of the POST-preprocessing model-input vector (+ raw
and calibrated win) so the inference is fully reproducible/auditable even for TE models.
Session-independent: the caller supplies the as-of feature rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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


#: Feature 091: same-day columns dropped together when a race is only partially weighed.
#: Kept in sync with features.weight_mask.WEIGHT_MASK_COLUMNS via a test, not by import, so the
#: serving path does not gain a features dependency it does not otherwise need.
SAME_DAY_WEIGHT_COLUMNS = ("weight", "weight_diff", "carried_weight_ratio")

#: Column whose presence in a model's inputs means it has a fallback for a missing same-day weight.
PREV_WEIGHT_COLUMN = "prev_weight"


@dataclass(frozen=True)
class WeightAvailability:
    """Outcome of the race-level availability normalisation (FR-035 observability)."""

    rows: pd.DataFrame
    applicable: bool   # the model carries prev_weight, so the rule can fire at all
    normalised: bool   # the rule actually fired for this race
    n_started: int
    n_weighed: int


def normalise_weight_availability(
    rows: pd.DataFrame, *, feature_cols: list[str]
) -> WeightAvailability:
    """Binarise same-day weight availability across a race (FR-034).

    ``rows`` is one race's started horses. Returns the (possibly modified) frame plus the counts
    needed to report how often full-info is being given up. A no-op for models without
    ``prev_weight`` (FR-034a) — those keep whatever weights they were given.
    """
    n_started = len(rows)
    if "weight" not in rows.columns:
        # No same-day weight column at all: availability is uniformly "absent" by construction,
        # so there is nothing to collapse (and nothing to report as given up).
        return WeightAvailability(rows, PREV_WEIGHT_COLUMN in feature_cols, False, n_started, 0)
    weighed = pd.to_numeric(rows["weight"], errors="coerce").notna()
    n_weighed = int(weighed.sum()) if n_started else 0

    if PREV_WEIGHT_COLUMN not in feature_cols:
        return WeightAvailability(rows, False, False, n_started, n_weighed)
    if n_weighed == n_started or n_weighed == 0:
        # already uniform: either full-info or fully unweighed. Nothing to collapse.
        return WeightAvailability(rows, True, False, n_started, n_weighed)

    out = rows.copy()
    present = [c for c in SAME_DAY_WEIGHT_COLUMNS if c in out.columns]
    out[present] = np.nan
    return WeightAvailability(out, True, True, n_started, n_weighed)


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

    # Feature 091 (FR-034/034a/034b): race-level availability normalisation. Training masks the
    # same-day weight columns RACE-ATOMICALLY, so a race where only some horses have been weighed
    # is out-of-distribution for the model — and because the objective is a within-race softmax,
    # one horse's input moves EVERY horse's probability. Collapse availability to the same binary
    # the model was trained on: if any started horse is unweighed, drop the same-day weight for the
    # whole race. Only for models that actually carry `prev_weight`; taking today's weight away
    # from a model with no fallback is an uncompensated loss (SC-004). Same rule on live and
    # backfill, since settled data still has ~0.3% missing weights.
    weight_normalised = normalise_weight_availability(rows, feature_cols=model.feature_cols)
    rows = weight_normalised.rows

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
