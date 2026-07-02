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

from .cond_logit import (
    cond_logit_objective,
    group_sizes_from_race_ids,
    pl_topk_objective,
    race_softmax,
)

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
    #: "binary" (per-horse P(win)) | "cond_logit" (race-softmax, 039) | "pl_topk" (PL top-3, 042).
    objective: str = "binary"
    booster_: lgb.LGBMClassifier | lgb.Booster | None = None
    feature_cols_: list[str] | None = None
    _constant: float | None = None

    #: objectives whose raw score is softmaxed within each race (identical predict path).
    SOFTMAX_OBJECTIVES = ("cond_logit", "pl_topk")

    def fit(
        self,
        X: pd.DataFrame,
        y,
        *,
        categorical_cols: list[str] | None = None,
        group_ids=None,
        ranks=None,
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
        cat = [c for c in (categorical_cols or []) if c in X.columns]
        if self.objective in self.SOFTMAX_OBJECTIVES:
            self._fit_softmax(X, y, cat, group_ids, ranks)
        else:
            clf = lgb.LGBMClassifier(
                random_state=self.seed,
                deterministic=True,
                num_threads=1,
                force_col_wise=True,
                verbose=-1,
                **self.params,
            )
            clf.fit(X, y, categorical_feature=cat or "auto")
            self.booster_ = clf
        return self

    def _fit_softmax(self, X, y, cat, group_ids, ranks) -> None:
        if group_ids is None:
            raise ValueError(f"{self.objective} objective requires group_ids (race ids)")
        if self.objective == "pl_topk" and ranks is None:
            raise ValueError("pl_topk objective requires ranks (finishing ranks 1..k/0)")
        # rows must be contiguous by race for the group softmax -> stable sort
        order = np.argsort(np.asarray(group_ids), kind="stable")
        Xs = X.iloc[order].reset_index(drop=True)
        ys = np.asarray(y, dtype=float)[order]
        gsizes = group_sizes_from_race_ids(np.asarray(group_ids)[order])
        if self.objective == "pl_topk":
            obj = pl_topk_objective(gsizes, np.asarray(ranks)[order])
        else:
            obj = cond_logit_objective(gsizes)

        params = {k: v for k, v in self.params.items() if k != "objective"}
        num_round = int(params.pop("n_estimators", 300))
        params.update(
            objective=obj,
            seed=self.seed,
            deterministic=True,
            num_threads=1,
            force_col_wise=True,
            verbose=-1,
        )
        dtrain = lgb.Dataset(
            Xs, label=ys, categorical_feature=cat or "auto", free_raw_data=False
        )
        self.booster_ = lgb.train(params, dtrain, num_boost_round=num_round)

    def predict(self, X: pd.DataFrame, *, group_ids=None) -> np.ndarray:
        """Per-horse win prob. binary -> P(win); cond_logit/pl_topk -> per-race softmax.

        Softmax objectives REQUIRE group_ids (race ids aligned to X rows) so the softmax
        normalizes within each race; None raises (group is mandatory at every entry).
        """
        if self.booster_ is None:
            const = 0.0 if self._constant is None else self._constant
            return np.full(len(X), const, dtype=float)
        if self.objective in self.SOFTMAX_OBJECTIVES:
            if group_ids is None:
                raise ValueError(f"{self.objective} predict requires group_ids (race ids)")
            gids = np.asarray(group_ids)
            order = np.argsort(gids, kind="stable")
            raw = self.booster_.predict(
                X[self.feature_cols_].iloc[order], raw_score=True
            )
            gsizes = group_sizes_from_race_ids(gids[order])
            p_sorted = race_softmax(raw, gsizes)
            out = np.empty(len(X), dtype=float)
            out[order] = p_sorted
            return out
        proba = self.booster_.predict_proba(X[self.feature_cols_])
        return np.asarray(proba[:, 1], dtype=float)

    def gain_importance(self) -> dict[str, float] | None:
        """Feature 040: {feature -> gain} split-gain importance, or None if degenerate.

        Handles both booster types: LGBMClassifier (binary, via .booster_) and the raw
        lgb.Booster (cond_logit). Keyed by feature_cols_ (includes TE columns).
        """
        if self.booster_ is None or self.feature_cols_ is None:
            return None
        raw = getattr(self.booster_, "booster_", self.booster_)  # unwrap sklearn wrapper
        gains = raw.feature_importance(importance_type="gain")
        return {f: float(g) for f, g in zip(self.feature_cols_, gains, strict=True)}
