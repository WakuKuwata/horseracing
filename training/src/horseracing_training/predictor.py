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
from .dataset import RACE_DATE, WIN_LABEL, TrainingMatrix, build_training_matrix
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
    ) -> None:
        self.session = session
        self.seed = seed
        self.calibration = calibration
        self.ece_clip = ece_clip
        self.params = params
        self.calib_frac = calib_frac

        self._data: TrainingMatrix | None = None
        self.win_model_: WinModel | None = None
        self.calibrator_: Calibrator | None = None
        self.feature_cols_: list[str] | None = None
        self.fit_info_: dict | None = None

    # --- data (built once, reused across folds) ------------------------------
    def _ensure_data(self) -> TrainingMatrix:
        if self._data is None:
            self._data = build_training_matrix(self.session)
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

        X = train_df[data.feature_cols]
        y = train_df[WIN_LABEL].to_numpy()

        self.win_model_ = WinModel(seed=self.seed, params=self._resolved_params()).fit(
            X[model_mask], y[model_mask], categorical_cols=data.categorical_cols
        )

        if calib_mask.any():
            raw_c = self.win_model_.predict(X[calib_mask])
            self.calibrator_ = fit_calibrator(
                raw_c, y[calib_mask], method=self.calibration, clip=self.ece_clip
            )
        else:
            self.calibrator_ = Calibrator(method="identity", clip=self.ece_clip, identity=True)

        self.fit_info_ = {
            "seed": self.seed,
            "params": self.win_model_.params,
            "calibration": self.calibrator_.method,
            "calib_frac": self.calib_frac,
            "n_train_rows": int(len(train_df)),
            "n_model_rows": int(model_mask.sum()),
            "n_calib_rows": int(calib_mask.sum()),
            "model_degenerate": self.win_model_.booster_ is None,
            "calibrator_degenerate": self.calibrator_.identity,
            "train_through": _max_date(train_df[RACE_DATE]),
            "feature_cols": list(data.feature_cols),
            "categorical_cols": list(data.categorical_cols),
        }

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
        X = rows[data.feature_cols]

        raw = self.win_model_.predict(X)
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
