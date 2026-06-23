"""US4 (P2): hyperparameter search inside TRAIN only (valid/test never enter).

Expanding, race-level, chronological CV — no race straddles a fold boundary. When target
encoding is enabled, the encoder is **refit on each CV train fold** and only *applied* to the
validation fold (codex's #1 trap: pre-encoding the whole frame then splitting leaks the
validation labels into their own encodings and inflates the CV score). The per-fold validation
score is therefore leak-free.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from .folds import chronological_race_folds
from .target_encoding import DEFAULT_SMOOTHING, apply_encoded_columns, fit_target_encoder
from .win_model import DEFAULT_PARAMS, WinModel

_CLIP = 1e-15


@dataclass(frozen=True)
class CVResult:
    best_params: dict
    scores: dict  # repr(params) -> mean log_loss


def _encode_fold(
    tr: pd.DataFrame,
    va: pd.DataFrame,
    feature_cols: list[str],
    te_cols: tuple[str, ...],
    label_col: str,
    smoothing: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit TE on the train fold only; transform both folds. va is never used to fit (no leak)."""
    tr_x, va_x = tr[feature_cols].copy(), va[feature_cols].copy()
    if te_cols:
        prior = float(tr[label_col].mean())
        tr_enc, va_enc = {}, {}
        for col in te_cols:
            enc = fit_target_encoder(tr, col, label_col=label_col, prior=prior, smoothing=smoothing)
            tr_enc[col] = enc.transform(tr[col])
            va_enc[col] = enc.transform(va[col])
        tr_x = apply_encoded_columns(tr_x, tr_enc, feature_cols)
        va_x = apply_encoded_columns(va_x, va_enc, feature_cols)
    return tr_x, va_x


def select_params_cv(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    race_id_col: str,
    race_date_col: str,
    label_col: str,
    grid: list[dict],
    categorical_cols: list[str] | None = None,
    target_encode_cols: tuple[str, ...] | None = None,
    te_smoothing: float = DEFAULT_SMOOTHING,
    seed: int = 42,
    n_splits: int = 3,
) -> CVResult:
    """Pick params minimizing mean expanding-CV log_loss over TRAIN only."""
    race_dates = dict(zip(df[race_id_col], df[race_date_col], strict=True))
    folds = chronological_race_folds(df[race_id_col].to_numpy(), race_dates, n_splits)
    te_cols = tuple(target_encode_cols or ())
    cat_for_model = [c for c in (categorical_cols or []) if c not in te_cols]

    scores: dict[str, float] = {}
    for params in grid:
        fold_losses: list[float] = []
        for i in range(1, len(folds)):
            train_races = set().union(*folds[:i])
            valid_races = folds[i]
            tr = df[df[race_id_col].isin(train_races)]
            va = df[df[race_id_col].isin(valid_races)]
            if tr.empty or va.empty or va[label_col].nunique() < 2:
                continue
            tr_x, va_x = _encode_fold(tr, va, feature_cols, te_cols, label_col, te_smoothing)
            model = WinModel(seed=seed, params={**DEFAULT_PARAMS, **params}).fit(
                tr_x, tr[label_col].to_numpy(), categorical_cols=cat_for_model
            )
            p = np.clip(model.predict(va_x), _CLIP, 1 - _CLIP)
            fold_losses.append(log_loss(va[label_col].to_numpy(), p, labels=[0, 1]))
        scores[repr(params)] = float(np.mean(fold_losses)) if fold_losses else float("inf")

    best = min(grid, key=lambda p: scores[repr(p)])
    return CVResult(best_params={**DEFAULT_PARAMS, **best}, scores=scores)


#: small default grid used when HPO is enabled from the CLI (kept tiny for runtime).
DEFAULT_GRID: list[dict] = [
    {"num_leaves": 15, "learning_rate": 0.05},
    {"num_leaves": 31, "learning_rate": 0.05},
    {"num_leaves": 63, "learning_rate": 0.03},
]
