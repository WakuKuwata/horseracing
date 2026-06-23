"""US4 (P2): hyperparameter search inside train, and out-of-fold target encoding.

Both tools operate strictly on TRAIN rows the caller supplies — valid/test never enter
(INV-T3 generalized to model selection). ``select_params_cv`` uses an expanding,
race-level, chronological CV so no race straddles a fold boundary. ``oof_target_encode``
produces out-of-fold means so a row's encoding is never fit on its own label
(fit-all-train -> apply-all-train would leak each row into its own feature).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from .win_model import DEFAULT_PARAMS, WinModel


def _chronological_race_folds(race_ids, race_dates, n_splits: int) -> list[set[str]]:
    uniq = sorted({rid: race_dates[rid] for rid in race_ids}.items(), key=lambda kv: (kv[1], kv[0]))
    race_order = [rid for rid, _ in uniq]
    if len(race_order) < n_splits + 1:
        n_splits = max(1, len(race_order) - 1)
    chunks = np.array_split(race_order, n_splits + 1)
    return [set(c.tolist()) for c in chunks]


@dataclass(frozen=True)
class CVResult:
    best_params: dict
    scores: dict  # repr(params) -> mean log_loss


def select_params_cv(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    race_id_col: str,
    race_date_col: str,
    label_col: str,
    grid: list[dict],
    categorical_cols: list[str] | None = None,
    seed: int = 42,
    n_splits: int = 3,
) -> CVResult:
    """Pick params minimizing mean expanding-CV log_loss over TRAIN only."""
    race_dates = dict(zip(df[race_id_col], df[race_date_col], strict=True))
    folds = _chronological_race_folds(df[race_id_col].to_numpy(), race_dates, n_splits)

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
            model = WinModel(seed=seed, params={**DEFAULT_PARAMS, **params}).fit(
                tr[feature_cols], tr[label_col].to_numpy(), categorical_cols=categorical_cols
            )
            p = np.clip(model.predict(va[feature_cols]), 1e-15, 1 - 1e-15)
            fold_losses.append(log_loss(va[label_col].to_numpy(), p, labels=[0, 1]))
        scores[repr(params)] = float(np.mean(fold_losses)) if fold_losses else float("inf")

    best = min(grid, key=lambda p: scores[repr(p)])
    return CVResult(best_params={**DEFAULT_PARAMS, **best}, scores=scores)


def oof_target_encode(
    df: pd.DataFrame,
    col: str,
    *,
    race_id_col: str,
    race_date_col: str,
    label_col: str,
    n_splits: int = 5,
    smoothing: float = 10.0,
) -> pd.Series:
    """Out-of-fold mean target encoding for a categorical column (no self-leak).

    Each race-level fold's rows are encoded using the *other* folds' label means only,
    blended toward the global prior with ``smoothing`` (handles unseen/rare categories).
    """
    race_dates = dict(zip(df[race_id_col], df[race_date_col], strict=True))
    folds = _chronological_race_folds(df[race_id_col].to_numpy(), race_dates, n_splits)
    prior = float(df[label_col].mean())
    out = pd.Series(np.full(len(df), prior, dtype=float), index=df.index)

    for held in folds:
        held_mask = df[race_id_col].isin(held)
        rest = df[~held_mask]
        if rest.empty:
            continue
        agg = rest.groupby(col, observed=True)[label_col].agg(["sum", "count"])
        enc = (agg["sum"] + smoothing * prior) / (agg["count"] + smoothing)
        out.loc[held_mask] = df.loc[held_mask, col].map(enc).fillna(prior).to_numpy()
    return out
