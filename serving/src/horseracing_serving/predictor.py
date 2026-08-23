"""Serving inference for one race (contracts/serving.md, INV-S1..S3).

Order: started-align -> apply target encoders -> booster raw -> calibrate -> clip ->
race-normalize -> Harville (all reused from training's pure parts). Returns a Prediction per
started horse, a per-horse snapshot of the POST-preprocessing model-input vector (+ raw and
calibrated win), explanations, and the race_class vocabulary audit.
Session-independent: the caller supplies the as-of feature rows.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from horseracing_eval.predictor import Prediction
from horseracing_features.race_class_canon import canonicalise
from horseracing_training.calibration import DEFAULT_CLIP
from horseracing_training.explanation import compute_explanations
from horseracing_training.predictor import assemble_predictions
from horseracing_training.target_encoding import apply_encoded_columns
from horseracing_training.win_model import WinModel

from .model_loader import ServingError, ServingModel

_LOGGER = logging.getLogger(__name__)


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


def race_weight_availability(
    feature_rows: pd.DataFrame, race_id: str, *, model: ServingModel
) -> WeightAvailability:
    """Availability of one race, from the SAME slice + rule ``predict_race`` uses.

    The pipeline needs this to stamp the regime marker and to report how often full-info is being
    given up. It must not re-derive the answer independently: a marker that disagrees with the
    input the model actually received is worse than no marker, because it would be trusted when
    filtering. So both callers go through this one function.
    """
    rows = feature_rows[feature_rows["race_id"] == race_id]
    if rows.empty:
        return WeightAvailability(rows, PREV_WEIGHT_COLUMN in model.feature_cols, False, 0, 0)
    return normalise_weight_availability(rows, feature_cols=model.feature_cols)


def predict_race(
    model: ServingModel, race_id: str, feature_rows: pd.DataFrame, *,
    stage_discount=None, win_odds: dict[str, float | None] | None = None,
) -> tuple[dict[str, Prediction], dict[str, dict], dict[str, dict | None], dict]:
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

    # Feature 098 (INV-R4/R7): representation and vocabulary audit happen before pandas category
    # coercion. Unknown values are observed but kept for LightGBM's missing-category path; a new,
    # legitimate class label must not stop the whole serving run.
    race_class_nan_before = (
        int(rows["race_class"].isna().sum()) if "race_class" in rows.columns else 0
    )
    if model.race_class_representation == "canonical-v1" and "race_class" in rows.columns:
        rows["race_class"], _ = canonicalise(rows["race_class"])

    race_class_vocab = model.categorical_vocab.get("race_class", [])
    if not race_class_vocab or "race_class" not in rows.columns:
        n_unknown: int | None = None
        unknown_values: list[str] = []
    else:
        unknown = rows["race_class"].notna() & ~rows["race_class"].isin(race_class_vocab)
        n_unknown = int(unknown.sum())
        unknown_values = sorted(str(v) for v in rows.loc[unknown, "race_class"].unique())
        if len(rows) and n_unknown / len(rows) > 0.01:
            _LOGGER.warning(
                "race_class unknown rate %.2f%% exceeds 1%% for model=%s representation=%s "
                "unknown_values=%s",
                100.0 * n_unknown / len(rows),
                model.model_version,
                model.race_class_representation,
                unknown_values,
            )
    audit = {
        "n_unknown": n_unknown,
        "unknown_values": unknown_values,
        "representation": model.race_class_representation,
        "feature_version": model.feature_version,
        "n_rows": len(rows),
    }

    # Match training's dtype coercion (build_feature_matrix leaves raw object/Decimal columns,
    # but the booster was trained on category + numeric like build_training_matrix produces).
    for col in model.categorical_cols:
        if col in rows.columns:
            rows[col] = rows[col].astype("category")
    if (
        "race_class" in rows.columns
        and int(rows["race_class"].isna().sum()) > race_class_nan_before
    ):
        raise ServingError("race_class NaN count increased during representation/category coercion")
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
    return predictions, snapshots, explanations, audit
