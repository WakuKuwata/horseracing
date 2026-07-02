"""LightGBMPredictor — the Feature 003 Predictor contract (contracts/predictor.md).

Inference order (INV-T1): raw win -> calibrate -> clip([eps,1-eps]) -> race-normalize(Σ=1)
-> Harville top2/top3. A Prediction is returned for EVERY started horse (codex: the harness
silently skips missing ids, so partial output would bias metrics). top2/top3 reuse
``horseracing_eval.baselines.harville_topk`` so they match the market baseline exactly (R8).

Leak safety:
- Features come only from ``model_input_features()`` (INV-T4). ``RaceContext.started_horses[]
  .result_market`` (result-time odds/popularity) is never read.
- The full matrix is built once with ``end_date=None``; history is as-of each row's own
  race_date, so train-row features don't depend on later races (research R4, no TE in MVP).
- The calibrator is fit on a train-internal held-out slice only (INV-T3).
"""

from __future__ import annotations

import datetime

import numpy as np
from horseracing_eval.baselines import harville_topk
from horseracing_eval.predictor import Prediction, RaceContext
from sqlalchemy.orm import Session

from .calibration import (
    DEFAULT_CALIB_FRAC,
    DEFAULT_CLIP,
    Calibrator,
    fit_calibrator,
    split_train_by_time,
)
from .dataset import RACE_DATE, RANK_LABEL, WIN_LABEL, TrainingMatrix, build_training_matrix
from .hpo import select_params_cv
from .target_encoding import (
    DEFAULT_SMOOTHING,
    TargetEncoder,
    apply_encoded_columns,
    fit_target_encoder,
    oof_target_encode,
)
from .win_model import WinModel


class LightGBMPredictor:
    #: never references result-time odds/popularity (FR-004)
    is_leaky_reference = False

    def __init__(
        self,
        session: Session,
        *,
        seed: int = 42,
        calibration: str = "platt",
        ece_clip: float = DEFAULT_CLIP,
        params: dict | None = None,
        calib_frac: float = DEFAULT_CALIB_FRAC,
        hpo: bool = False,
        param_grid: list[dict] | None = None,
        hpo_splits: int = 3,
        target_encode_cols: tuple[str, ...] = (),
        te_smoothing: float = DEFAULT_SMOOTHING,
        drop_features: tuple[str, ...] = (),
        objective: str = "binary",
    ) -> None:
        self.session = session
        self.seed = seed
        # Feature 039/042: "binary" | "cond_logit" (race-softmax) | "pl_topk" (PL top-3).
        self.objective = objective
        self.calibration = calibration
        self.ece_clip = ece_clip
        self.params = params
        self.calib_frac = calib_frac
        # US4 (P2), opt-in. Defaults preserve the validated MVP path bit-for-bit.
        self.hpo = hpo
        self.param_grid = param_grid
        self.hpo_splits = hpo_splits
        self.target_encode_cols = tuple(target_encode_cols)
        self.te_smoothing = te_smoothing
        # Feature 020: exclude these feature columns (e.g. to build a baseline without the new
        # features). Empty = no-op (validated MVP path unchanged bit-for-bit).
        self.drop_features = tuple(drop_features)

        self._data: TrainingMatrix | None = None
        self.win_model_: WinModel | None = None
        self.calibrator_: Calibrator | None = None
        self.feature_cols_: list[str] | None = None
        self.encoders_: dict[str, TargetEncoder] = {}
        self.te_cols_: tuple[str, ...] = ()
        self.fit_info_: dict | None = None

    # --- data (built once, reused across folds) ------------------------------
    def _ensure_data(self) -> TrainingMatrix:
        if self._data is None:
            full = build_training_matrix(self.session)
            if self.drop_features:
                # Feature 020: drop excluded columns from feature_cols (frame keeps them, unused).
                # All downstream uses data.feature_cols, so a single filter propagates everywhere.
                import dataclasses
                keep = [c for c in full.feature_cols if c not in self.drop_features]
                full = dataclasses.replace(
                    full,
                    feature_cols=keep,
                    categorical_cols=[c for c in full.categorical_cols if c in keep],
                )
            self._data = full
        return self._data

    # --- fit -----------------------------------------------------------------
    def fit(self, train_races: list[RaceContext]) -> None:
        data = self._ensure_data()
        self.feature_cols_ = data.feature_cols
        train_ids = {rc.race_id for rc in train_races}
        df = data.frame
        train_df = df[df["race_id"].isin(train_ids)].reset_index(drop=True)
        if train_df.empty:
            raise ValueError("no training rows for the given train_races")

        race_dates = dict(zip(train_df["race_id"], train_df[RACE_DATE], strict=True))
        model_mask, calib_mask = split_train_by_time(
            train_df["race_id"].to_numpy(), race_dates, calib_frac=self.calib_frac
        )
        model_df = train_df[model_mask].reset_index(drop=True)
        calib_df = train_df[calib_mask].reset_index(drop=True)
        y_model = model_df[WIN_LABEL].to_numpy()

        # --- target encoding (opt-in): encoders fit on model-fit rows only --------------
        self.te_cols_ = tuple(c for c in self.target_encode_cols if c in data.feature_cols)
        self.encoders_ = {}
        cat_for_model = [c for c in data.categorical_cols if c not in self.te_cols_]
        model_X = model_df[data.feature_cols].copy()
        calib_X = calib_df[data.feature_cols].copy()
        if self.te_cols_:
            prior = float(y_model.mean())  # shared by OOF / final encoder / predict fallback
            model_enc: dict[str, np.ndarray] = {}
            calib_enc: dict[str, np.ndarray] = {}
            for col in self.te_cols_:
                self.encoders_[col] = fit_target_encoder(
                    model_df, col, label_col=WIN_LABEL, prior=prior, smoothing=self.te_smoothing
                )
                # training rows: out-of-fold (a row is never encoded by its own label)
                model_enc[col] = oof_target_encode(
                    model_df, col, race_id_col="race_id", race_date_col=RACE_DATE,
                    label_col=WIN_LABEL, prior=prior, smoothing=self.te_smoothing,
                ).to_numpy()
                # held-out calibration rows: apply the final (model-fit) encoder
                if not calib_df.empty:
                    calib_enc[col] = self.encoders_[col].transform(calib_df[col])
            model_X = apply_encoded_columns(model_X, model_enc, data.feature_cols)
            if not calib_df.empty:
                calib_X = apply_encoded_columns(calib_X, calib_enc, data.feature_cols)

        # --- hyperparameter selection (opt-in): train-internal CV, valid never seen ------
        params = self._select_params(model_df, data, cat_for_model)

        # Feature 039/042: softmax objectives need race groups at fit AND predict (softmax
        # unit). The model-fit rows' race_ids are the training groups; the held-out calib
        # rows' race_ids group the calibration predictions (never softmax across races).
        is_softmax = self.objective in WinModel.SOFTMAX_OBJECTIVES
        model_groups = model_df["race_id"].to_numpy() if is_softmax else None
        calib_groups = (
            calib_df["race_id"].to_numpy() if (is_softmax and not calib_df.empty) else None
        )
        # Feature 042: pl_topk consumes the finishing-rank LABEL (1..3/0, never a feature)
        model_ranks = (
            model_df[RANK_LABEL].to_numpy() if self.objective == "pl_topk" else None
        )
        self.win_model_ = WinModel(
            seed=self.seed, params=params, objective=self.objective
        ).fit(
            model_X, y_model, categorical_cols=cat_for_model,
            group_ids=model_groups, ranks=model_ranks,
        )

        if calib_mask.any():
            raw_c = self.win_model_.predict(calib_X, group_ids=calib_groups)
            self.calibrator_ = fit_calibrator(
                raw_c, calib_df[WIN_LABEL].to_numpy(), method=self.calibration, clip=self.ece_clip
            )
        else:
            self.calibrator_ = Calibrator(method="identity", clip=self.ece_clip, identity=True)

        self.fit_info_ = {
            "seed": self.seed,
            "objective": self.objective,
            "postprocess": (
                "group_softmax"
                if self.objective in WinModel.SOFTMAX_OBJECTIVES else "sigmoid"
            ),
            "params": self.win_model_.params,
            "calibration": self.calibrator_.method,
            "calib_frac": self.calib_frac,
            "hpo": self.hpo,
            "target_encode_cols": list(self.te_cols_),
            "te_smoothing": self.te_smoothing if self.te_cols_ else None,
            "n_train_rows": int(len(train_df)),
            "n_model_rows": int(model_mask.sum()),
            "n_calib_rows": int(calib_mask.sum()),
            "model_degenerate": self.win_model_.booster_ is None,
            "calibrator_degenerate": self.calibrator_.identity,
            "train_through": _max_date(train_df[RACE_DATE]),
            "feature_cols": list(data.feature_cols),
            "categorical_cols": list(cat_for_model),
        }

    def _select_params(self, model_df, data: TrainingMatrix, cat_for_model: list[str]) -> dict:
        if not self.hpo:
            return self._resolved_params()
        if self.objective in WinModel.SOFTMAX_OBJECTIVES:  # 039/042: HPO deferred
            raise NotImplementedError(f"HPO is not supported with objective={self.objective}")
        grid = self.param_grid
        if grid is None:
            from .hpo import DEFAULT_GRID

            grid = DEFAULT_GRID
        result = select_params_cv(
            model_df, data.feature_cols,
            race_id_col="race_id", race_date_col=RACE_DATE, label_col=WIN_LABEL,
            grid=grid, categorical_cols=data.categorical_cols,
            target_encode_cols=self.te_cols_, te_smoothing=self.te_smoothing,
            seed=self.seed, n_splits=self.hpo_splits,
        )
        return result.best_params

    def _resolved_params(self) -> dict:
        from .win_model import DEFAULT_PARAMS

        return dict(self.params) if self.params is not None else dict(DEFAULT_PARAMS)

    # --- predict -------------------------------------------------------------
    def predict_race(self, race: RaceContext) -> dict[str, Prediction]:
        if self.win_model_ is None or self.calibrator_ is None:
            raise RuntimeError("predict_race called before fit")
        data = self._ensure_data()
        started_ids = [h.horse_id for h in race.started_horses]

        rows = (
            data.frame[data.frame["race_id"] == race.race_id]
            .set_index("horse_id")
            .reindex(started_ids)  # exact coverage + started order; missing -> NaN features
        )
        X = rows[data.feature_cols].copy()
        if self.encoders_:  # apply the train-fit target encoders (category -> float)
            encoded = {col: enc.transform(rows[col]) for col, enc in self.encoders_.items()}
            X = apply_encoded_columns(X, encoded, data.feature_cols)

        # softmax objectives: all started horses of ONE race form a single softmax group.
        group_ids = (
            [race.race_id] * len(started_ids)
            if self.objective in WinModel.SOFTMAX_OBJECTIVES else None
        )
        raw = self.win_model_.predict(X, group_ids=group_ids)
        cal = self.calibrator_.transform(raw)
        return assemble_predictions(started_ids, cal, eps=self.ece_clip)


def assemble_predictions(
    started_ids: list[str], calibrated, *, eps: float = DEFAULT_CLIP
) -> dict[str, Prediction]:
    """clip -> race-normalize(Σwin=1) -> Harville top2/top3 (INV-T1 tail).

    Pure and DB-free: given calibrated raw win scores aligned to ``started_ids``, produce a
    consistency-satisfying Prediction per horse (0<=win<=top2<=top3<=1, Σ within tolerance).
    Endpoints are clipped so no win reaches 0/1 (keeps Harville top3 well-defined, R5).
    """
    cal = np.clip(np.asarray(calibrated, dtype=float), eps, 1.0 - eps)
    total = float(cal.sum())
    if total <= 0.0:  # defensive; clipped entries are > 0 so this normally can't trigger
        win = np.full(len(started_ids), 1.0 / len(started_ids))
    else:
        win = cal / total
    top2, top3 = harville_topk(win.tolist())
    return {
        hid: Prediction(win=float(win[i]), top2=float(top2[i]), top3=float(top3[i]))
        for i, hid in enumerate(started_ids)
    }


def _max_date(series) -> str | None:
    vals = [v for v in series if v is not None]
    if not vals:
        return None
    m = max(vals)
    if isinstance(m, datetime.date):
        return m.isoformat()
    return str(m)
