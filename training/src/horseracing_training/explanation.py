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
METHOD_VERSION = 1
DEFAULT_TOP_K = 5
#: relative tolerance for the additivity self-check (base + Σcontrib == raw score)
RECON_RTOL = 1e-6


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
) -> list[dict | None]:
    """Per-row top-K score-contribution explanation (one dict per row of X, aligned by position).

    Returns None for a row whose additivity self-check fails (never silently emit a wrong
    explanation; the prediction itself is unaffected — caller stores NULL for that row).
    """
    if len(X) == 0:
        return []
    n_feat = len(feature_cols)
    try:
        contrib = np.asarray(booster.predict(X[feature_cols], pred_contrib=True), dtype=float)
    except Exception:  # noqa: BLE001 — explanation must never break the prediction pipeline
        return [None] * len(X)
    # contrib shape = (n_rows, n_features + 1); last column is the base (expected) value.
    if contrib.ndim != 2 or contrib.shape[1] != n_feat + 1:
        # unexpected shape (e.g. multiclass) -> no explanation rather than a wrong one
        return [None] * len(X)

    out: list[dict | None] = []
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
            out.append(None)
            continue
        out.append(
            {
                "method": METHOD,
                "method_version": METHOD_VERSION,
                "k": k,
                "base_value": base,
                "score": score,
                "other_contribution": other,
                "items": items,
            }
        )
    return out
