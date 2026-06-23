"""Single win LightGBM (binary). Deterministic: fixed seed, single-thread, no bagging RNG.

MVP uses fixed hyperparameters (no search — that is US4/P2). Categorical inputs are
passed as pandas ``category`` dtype; LightGBM handles them natively and missing values
stay distinct from 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import pandas as pd

#: fixed, deterministic defaults. ``num_threads=1`` + ``deterministic=True`` make
#: training bit-reproducible for a given seed (SC-006).
DEFAULT_PARAMS: dict = {
    "objective": "binary",
    "n_estimators": 300,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "reg_lambda": 1.0,
}


@dataclass
class WinModel:
    seed: int = 42
    params: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    booster_: lgb.LGBMClassifier | None = None
    feature_cols_: list[str] | None = None
    _constant: float | None = None

    def fit(
        self, X: pd.DataFrame, y, *, categorical_cols: list[str] | None = None
    ) -> WinModel:
        self.feature_cols_ = list(X.columns)
        y = np.asarray(y)
        # Degenerate single-class training data: a classifier is undefined, so fall
        # back to the constant base rate. Calibration + race-normalization still yield
        # a consistent (uniform) prediction. Recorded so callers can see it happened.
        if len(np.unique(y)) < 2:
            self._constant = float(y.mean()) if len(y) else 0.0
            self.booster_ = None
            return self

        self._constant = None
        clf = lgb.LGBMClassifier(
            random_state=self.seed,
            deterministic=True,
            num_threads=1,
            force_col_wise=True,
            verbose=-1,
            **self.params,
        )
        cat = [c for c in (categorical_cols or []) if c in X.columns]
        clf.fit(X, y, categorical_feature=cat or "auto")
        self.booster_ = clf
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Raw P(win) per row in [0, 1]."""
        if self.booster_ is None:
            const = 0.0 if self._constant is None else self._constant
            return np.full(len(X), const, dtype=float)
        proba = self.booster_.predict_proba(X[self.feature_cols_])
        return np.asarray(proba[:, 1], dtype=float)
