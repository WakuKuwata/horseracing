"""Feature 040: per-horse score-contribution explanation from a LightGBM booster.

``compute_explanations`` runs ``booster.predict(X, pred_contrib=True)`` (TreeSHAP, built into
LightGBM — no new dependency) and returns, per row, the top-K contributions to the booster's
RAW score margin (``predict(raw_score=True)``) as a fixed-schema, JSON-serialisable dict:

    {method, method_version, k, base_value, score, other_contribution,
     items: [{feature, value, contribution}]}

The decomposition is additive on the RAW margin (INV-E1): base_value + Σ all contributions == score.
This margin is BEFORE the 039 race-softmax / isotonic calibration / 009 normalisation — the display
layer must frame it as "score contribution", not a breakdown of the final probability.

X must be the SAME matrix serving feeds the booster (target-encoded columns applied,
``feature_cols`` order) so the explanation matches the served prediction exactly. The T0 spike
(research.md) verified additivity holds to machine precision for the cond_logit booster.
"""

from __future__ import annotations

import math

import lightgbm as lgb
import numpy as np
import pandas as pd

METHOD = "lgbm_pred_contrib"
METHOD_VERSION_V1 = 1
METHOD_VERSION_V2 = 2
DEFAULT_TOP_K = 5
#: Relative tolerance for additivity checks (base + Σcontrib == raw score).
RECON_RTOL = 1e-6
#: INV-E4 absolute tolerance grows with the race population size.
CENTER_SUM_ATOL_PER_ROW = 1e-9


def _jsonable(v):
    """Match serving.predictor._jsonable: NaN->None, numpy->py, category->str."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, np.floating):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, float):
        return v
    if pd.isna(v):
        return None
    return str(v)


def compute_explanations(
    booster: lgb.Booster,
    X: pd.DataFrame,
    feature_cols: list[str],
    *,
    k: int = DEFAULT_TOP_K,
    center_within_group: bool = False,
    expected_raw_scores: np.ndarray | None = None,
) -> list[dict | None]:
    """Per-row top-K score-contribution explanation (one dict per row of X, aligned by position).

    The default path preserves the v1 raw-contribution output.  When ``center_within_group`` is
    true, feature contributions are centered across all rows and v2 items are selected by the
    centered magnitude after excluding features whose values are constant within the group.

    Explanation failures never escape to the prediction path.  v1 retains row-local validation;
    v2 is group-atomic because a partial population would make the centering invalid.
    """
    if len(X) == 0:
        return []
    n_feat = len(feature_cols)
    try:
        contrib = np.asarray(booster.predict(X[feature_cols], pred_contrib=True), dtype=float)
        # contrib shape = (n_rows, n_features + 1); last column is the base (expected) value.
        if contrib.ndim != 2 or contrib.shape[1] != n_feat + 1:
            # unexpected shape (e.g. multiclass) -> no explanation rather than a wrong one
            return [None] * len(X)

        expected = None
        if expected_raw_scores is not None:
            expected = np.asarray(expected_raw_scores, dtype=float)
            if expected.shape != (len(X),):
                return [None] * len(X)

        if not center_within_group:
            # Keep this branch byte-for-byte compatible with the v1 output, including key order,
            # float operations, top-K selection, and row-local self-check failure.
            out_v1: list[dict | None] = []
            for i in range(len(X)):
                feat_contrib = contrib[i, :n_feat]
                base = float(contrib[i, n_feat])
                total = float(feat_contrib.sum())
                score = base + total
                # decreasing |contribution|, then feature name ascending (deterministic, INV-E3)
                order = sorted(
                    range(n_feat), key=lambda j: (-abs(feat_contrib[j]), feature_cols[j])
                )
                top_idx = order[:k]
                items = [
                    {
                        "feature": feature_cols[j],
                        "value": _jsonable(X.iloc[i][feature_cols[j]]),
                        "contribution": float(feat_contrib[j]),
                    }
                    for j in top_idx
                ]
                other = float(sum(feat_contrib[j] for j in order[k:]))
                # INV-E1 self-check: base + Σtop + other == score
                recon = base + sum(it["contribution"] for it in items) + other
                if abs(recon - score) > RECON_RTOL * (abs(score) + 1e-9):
                    out_v1.append(None)
                    continue
                if expected is not None and not np.isclose(
                    score,
                    expected[i],
                    rtol=RECON_RTOL,
                    atol=RECON_RTOL * 1e-9,
                ):
                    out_v1.append(None)
                    continue
                out_v1.append(
                    {
                        "method": METHOD,
                        "method_version": METHOD_VERSION_V1,
                        "k": k,
                        "base_value": base,
                        "score": score,
                        "other_contribution": other,
                        "items": items,
                    }
                )
            return out_v1

        # v2 centering must use the complete race population.  Any invalid row invalidates all
        # explanations for the group rather than silently changing the population (INV-E1).
        if contrib.shape[0] != len(X) or not np.isfinite(contrib).all():
            return [None] * len(X)
        raw_from_contrib = contrib[:, n_feat] + contrib[:, :n_feat].sum(axis=1)
        if expected is not None and (
            not np.isfinite(expected).all()
            or not np.allclose(
                raw_from_contrib,
                expected,
                rtol=RECON_RTOL,
                atol=RECON_RTOL * 1e-9,
            )
        ):
            return [None] * len(X)

        feature_contrib = contrib[:, :n_feat]
        centered = feature_contrib - feature_contrib.mean(axis=0)
        center_sum_atol = CENTER_SUM_ATOL_PER_ROW * len(X)
        if not np.allclose(
            centered.sum(axis=0),
            np.zeros(n_feat),
            rtol=0.0,
            atol=center_sum_atol,
        ):
            return [None] * len(X)

        # A feature with one shared value cannot distinguish horses in this race.  All-missing is
        # shared; a missing/non-missing mixture is explicitly not shared.
        candidate_idx: list[int] = []
        for j, feature in enumerate(feature_cols):
            values = X[feature]
            missing = values.isna()
            all_equal = bool(missing.all()) or (
                not bool(missing.any()) and values.nunique(dropna=False) <= 1
            )
            if not all_equal:
                candidate_idx.append(j)

        out_v2: list[dict | None] = []
        for i in range(len(X)):
            feat_contrib = feature_contrib[i]
            centered_row = centered[i]
            base = float(contrib[i, n_feat])
            total = float(feat_contrib.sum())
            score = base + total
            order = sorted(
                candidate_idx,
                key=lambda j: (-abs(centered_row[j]), feature_cols[j]),
            )
            top_idx = order[:k]
            top_idx_set = set(top_idx)
            items = [
                {
                    "feature": feature_cols[j],
                    "value": _jsonable(X.iloc[i][feature_cols[j]]),
                    "contribution": float(feat_contrib[j]),
                    "contribution_centered": float(centered_row[j]),
                }
                for j in top_idx
            ]
            other = float(sum(feat_contrib[j] for j in range(n_feat) if j not in top_idx_set))
            recon = base + sum(it["contribution"] for it in items) + other
            if abs(recon - score) > RECON_RTOL * (abs(score) + 1e-9):
                return [None] * len(X)

            score_centered = float(centered_row.sum())
            other_centered = float(
                score_centered - sum(it["contribution_centered"] for it in items)
            )
            out_v2.append(
                {
                    "method": METHOD,
                    "method_version": METHOD_VERSION_V2,
                    "k": k,
                    "base_value": base,
                    "score": score,
                    "other_contribution": other,
                    "score_centered": score_centered,
                    "other_contribution_centered": other_centered,
                    "centering_population_size": len(X),
                    "items": items,
                }
            )
        return out_v2
    except Exception:  # noqa: BLE001 — explanation must never break the prediction pipeline
        return [None] * len(X)
