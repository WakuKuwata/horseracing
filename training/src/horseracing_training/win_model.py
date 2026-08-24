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
#: training bit-reproducible for a given seed (SC-006). The fit calls also force
#: ``force_row_wise=True``: a memory-layout choice that is BYTE-IDENTICAL to
#: ``force_col_wise`` (verified maxΔ=0.0 over binary/cond_logit/pl_topk on the real DB)
#: but ~1.6x faster on this many-row/wide pool. Raising num_threads was rejected: with the
#: custom softmax objectives it breaks cross-thread reproducibility (nt=4 shifted per-horse
#: p by ~2.5e-2), so num_threads stays 1.
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


def _group_stage_scales(margin_scales: np.ndarray, group_sizes: list[int]) -> np.ndarray:
    """Row-aligned (n_rows, 2) stage-2/3 scales -> validated per-group (n_groups, 3), s1=1.0.

    Feature 099 (INV-MT3): the scales are RACE-CONSTANT label-side aux values. Extracting the
    group's first row is only safe after proving the constancy, so every violation raises
    ``ValueError`` (never ``assert`` — ``-O`` strips asserts, and an unvalidated first-row read
    would hide a single corrupted row). Range [GMIN, 1.0] and finiteness are part of the frozen
    V1 contract; s1 is fixed at 1.0 (the winner stage is never modulated).
    """
    if margin_scales.ndim != 2 or margin_scales.shape[1] != 2:
        raise ValueError(
            f"margin_scales must have shape (n_rows, 2) for stages 2,3; got "
            f"{margin_scales.shape} (fail-closed)"
        )
    n = int(np.sum(group_sizes))
    if len(margin_scales) != n:
        raise ValueError(
            f"margin_scales rows ({len(margin_scales)}) != total group rows ({n}) (fail-closed)"
        )
    if not np.isfinite(margin_scales).all():
        raise ValueError("margin_scales contain non-finite values (fail-closed)")
    if margin_scales.min() < 0.25 - 1e-12 or margin_scales.max() > 1.0 + 1e-12:
        raise ValueError(
            f"margin_scales outside [0.25, 1.0]: min={margin_scales.min()} "
            f"max={margin_scales.max()} (fail-closed)"
        )
    group_start = np.concatenate(([0], np.cumsum(group_sizes)[:-1])).astype(np.intp)
    for col in range(2):
        col_vals = margin_scales[:, col]
        gmin = np.minimum.reduceat(col_vals, group_start)
        gmax = np.maximum.reduceat(col_vals, group_start)
        if not np.array_equal(gmin, gmax):
            bad = int(np.nonzero(gmin != gmax)[0][0])
            raise ValueError(
                f"margin_scales stage {col + 2} not race-constant within group {bad} "
                "(a single corrupted row must fail loudly, not be hidden by first-row "
                "extraction — INV-MT3)"
            )
    n_groups = len(group_sizes)
    out = np.ones((n_groups, 3), dtype=float)
    out[:, 1] = margin_scales[group_start, 0]
    out[:, 2] = margin_scales[group_start, 1]
    return out


@dataclass
class WinModel:
    seed: int = 42
    params: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    #: "binary" (per-horse P(win)) | "cond_logit" (race-softmax, 039) | "pl_topk" (PL top-3, 042).
    objective: str = "binary"
    booster_: lgb.LGBMClassifier | lgb.Booster | None = None
    feature_cols_: list[str] | None = None
    _constant: float | None = None
    #: Feature 060: True when fit with market offsets — predict then REQUIRES offsets
    #: (and vice versa), so a mismatch fails closed instead of silently dropping the
    #: market base from the score.
    offset_trained_: bool = False

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
        offsets=None,
        weights=None,
        margin_scales=None,
    ) -> WinModel:
        """Fit the win model.

        Feature 079: ``weights`` is an optional row-aligned LightGBM sample-weight vector.
        ``weights=None`` is BYTE-IDENTICAL to the pre-079 path (Dataset weight defaults to
        None → the objectives' ``get_weight()`` multiply is a no-op). The EV-weighting
        experiment passes a weight that is *constant within each race* (a per-race scalar
        α_r), which keeps the PL loss a valid weighted likelihood L_r'=α_r·L_r; that
        race-constancy invariant is enforced + tested where the weight is BUILT (leak/
        validity boundary), not here — this primitive just threads a generic per-row weight.
        """
        self.feature_cols_ = list(X.columns)
        if offsets is not None and self.objective not in self.SOFTMAX_OBJECTIVES:
            raise ValueError("offsets require a softmax objective (cond_logit/pl_topk)")
        # Feature 099: margin-aware teacher scales are defined only for the staged PL objective —
        # cond_logit has a single stage and binary has no stages, so accepting them there would
        # be a silent no-op (the exact failure mode this feature guards against).
        if margin_scales is not None and self.objective != "pl_topk":
            raise ValueError("margin_scales require objective='pl_topk' (fail-closed)")
        self.offset_trained_ = offsets is not None
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
            self._fit_softmax(X, y, cat, group_ids, ranks, offsets, weights, margin_scales)
        else:
            clf = lgb.LGBMClassifier(
                random_state=self.seed,
                deterministic=True,
                num_threads=1,
                force_row_wise=True,
                verbose=-1,
                **self.params,
            )
            w = None if weights is None else np.asarray(weights, dtype=float)
            clf.fit(X, y, sample_weight=w, categorical_feature=cat or "auto")
            self.booster_ = clf
        return self

    def _fit_softmax(
        self, X, y, cat, group_ids, ranks, offsets=None, weights=None, margin_scales=None
    ) -> None:
        if group_ids is None:
            raise ValueError(f"{self.objective} objective requires group_ids (race ids)")
        if self.objective == "pl_topk" and ranks is None:
            raise ValueError("pl_topk objective requires ranks (finishing ranks 1..k/0)")
        # rows must be contiguous by race for the group softmax -> stable sort
        order = np.argsort(np.asarray(group_ids), kind="stable")
        Xs = X.iloc[order].reset_index(drop=True)
        ys = np.asarray(y, dtype=float)[order]
        gsizes = group_sizes_from_race_ids(np.asarray(group_ids)[order])
        # Feature 060: offsets are sorted with the same order so they stay row-aligned
        off_sorted = np.asarray(offsets, dtype=float)[order] if offsets is not None else None
        # Feature 079: sample weights sorted with the same order (row-aligned to Xs/ys).
        w_sorted = np.asarray(weights, dtype=float)[order] if weights is not None else None
        # Feature 099: per-race stage scales (n_rows, 2) for stages 2,3 -> validated per-group
        # (n_groups, 3) with s1=1.0. None keeps the objective construction byte-identical.
        stage_scales = None
        if margin_scales is not None:
            stage_scales = _group_stage_scales(
                np.asarray(margin_scales, dtype=float)[order], gsizes
            )
        if self.objective == "pl_topk":
            obj = pl_topk_objective(
                gsizes, np.asarray(ranks)[order], offsets=off_sorted,
                stage_scales=stage_scales,
            )
        else:
            obj = cond_logit_objective(gsizes, offsets=off_sorted)

        params = {k: v for k, v in self.params.items() if k != "objective"}
        num_round = int(params.pop("n_estimators", 300))
        params.update(
            objective=obj,
            seed=self.seed,
            deterministic=True,
            num_threads=1,
            force_row_wise=True,
            verbose=-1,
        )
        dtrain = lgb.Dataset(
            Xs,
            label=ys,
            weight=w_sorted,
            categorical_feature=cat or "auto",
            free_raw_data=False,
        )
        self.booster_ = lgb.train(params, dtrain, num_boost_round=num_round)

    def predict(self, X: pd.DataFrame, *, group_ids=None, offsets=None) -> np.ndarray:
        """Per-horse win prob. binary -> P(win); cond_logit/pl_topk -> per-race softmax.

        Softmax objectives REQUIRE group_ids (race ids aligned to X rows) so the softmax
        normalizes within each race; None raises (group is mandatory at every entry).

        Feature 060: an offset-trained model REQUIRES row-aligned ``offsets`` (market
        log-q) — ``booster.predict(raw_score=True)`` returns only the tree sum, so the
        market base must be re-added here before the softmax. Mismatches fail closed in
        both directions (INV-M2/M4).
        """
        if self.offset_trained_ and offsets is None:
            raise ValueError("offset-trained model: predict requires offsets (fail-closed)")
        if not self.offset_trained_ and offsets is not None:
            raise ValueError("offsets passed to a model not trained with offsets")
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
            if offsets is not None:
                off = np.asarray(offsets, dtype=float)
                if not np.isfinite(off).all():
                    raise ValueError("predict: non-finite offsets (fail-closed)")
                raw = raw + off[order]
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
