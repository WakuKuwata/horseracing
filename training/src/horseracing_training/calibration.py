"""Probability calibration fit on a TRAIN-INTERNAL held-out slice (INV-T3, SC-002).

The calibration data is the latest ``calib_frac`` of *train* races by date — a race-level,
chronological split so no race straddles the model-fit / calibration-fit boundary (codex
point: row-level splits leak a race across both sides). valid/test races are never an input
here, so they cannot influence the calibrator (035/036 regression guard).

Platt (default) is a 1-D logistic on the raw score; isotonic is optional. Output is clipped
to ``[clip, 1-clip]`` so endpoints never reach 0/1 (keeps Harville top3 well-defined, R5).
Degenerate calibration slices (single class / too few rows) fall back to identity-with-clip,
recorded on the calibrator so callers/metadata can see it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

DEFAULT_CALIB_FRAC = 0.3
DEFAULT_CLIP = 1e-6
_MIN_CALIB_ROWS = 10


def split_train_by_time(
    race_ids, race_dates, *, calib_frac: float = DEFAULT_CALIB_FRAC
) -> tuple[np.ndarray, np.ndarray]:
    """Race-level chronological split of *train* rows.

    Returns ``(model_mask, calib_mask)`` boolean arrays aligned to the input rows.
    The latest ``calib_frac`` of distinct races (ordered by date, race_id) become the
    calibration-fit set; the earlier races are the model-fit set. Both sides hold whole
    races (disjoint race_id sets). If either side would be empty, calib_mask is all-False
    (caller falls back to identity calibration).
    """
    race_ids = np.asarray(race_ids)
    # order distinct races by (date, race_id) for determinism
    uniq = sorted({rid: race_dates[rid] for rid in race_ids}.items(), key=lambda kv: (kv[1], kv[0]))
    n_races = len(uniq)
    n_calib = int(round(n_races * calib_frac))
    if n_calib < 1 or n_calib >= n_races:
        # too few races to hold any out (or would consume everything) -> no calib
        if n_races >= 2:
            n_calib = 1
        else:
            return np.ones(len(race_ids), dtype=bool), np.zeros(len(race_ids), dtype=bool)
    calib_race_ids = {rid for rid, _ in uniq[n_races - n_calib :]}
    calib_mask = np.array([rid in calib_race_ids for rid in race_ids], dtype=bool)
    return ~calib_mask, calib_mask


def split_train_by_day(
    race_ids, race_dates, *, calib_frac: float = DEFAULT_CALIB_FRAC
) -> tuple[np.ndarray, np.ndarray]:
    """Feature 068 (FR-014b, codex C4): DAY-level chronological split.

    The latest ``calib_frac`` of distinct RACE-DAYS (not races) become the calibration set, so a
    single race-day never straddles the model-fit / calibration boundary (the race-count split
    ``split_train_by_time`` can split a day). This aligns the split unit with the block-bootstrap
    unit (open day). Returns ``(model_mask, calib_mask)``; if either side would be empty,
    ``calib_mask`` is all-False (caller falls back to identity calibration).
    """
    race_ids = np.asarray(race_ids)
    days = sorted({race_dates[rid] for rid in race_ids})
    n_days = len(days)
    n_calib_days = int(round(n_days * calib_frac))
    if n_calib_days < 1 or n_calib_days >= n_days:
        if n_days >= 2:
            n_calib_days = 1
        else:
            return np.ones(len(race_ids), dtype=bool), np.zeros(len(race_ids), dtype=bool)
    calib_days = set(days[n_days - n_calib_days:])
    calib_mask = np.array([race_dates[rid] in calib_days for rid in race_ids], dtype=bool)
    return ~calib_mask, calib_mask


@dataclass
class Calibrator:
    method: str  # 'platt' | 'isotonic' | 'identity'
    clip: float = DEFAULT_CLIP
    _platt: LogisticRegression | None = None
    _iso: IsotonicRegression | None = None
    identity: bool = False

    def transform(self, raw) -> np.ndarray:
        raw = np.clip(np.asarray(raw, dtype=float), self.clip, 1.0 - self.clip)
        if self.identity or self.method == "identity":
            return raw
        if self._platt is not None:
            out = self._platt.predict_proba(raw.reshape(-1, 1))[:, 1]
        elif self._iso is not None:
            out = self._iso.predict(raw)
        else:  # pragma: no cover - constructed but unfit
            out = raw
        return np.clip(np.asarray(out, dtype=float), self.clip, 1.0 - self.clip)

    def params_dict(self) -> dict:
        """Serializable parameters; used for metadata and the fold-leak equality check."""
        if self.identity or self.method == "identity":
            return {"method": "identity", "clip": self.clip}
        if self._platt is not None:
            return {
                "method": "platt",
                "clip": self.clip,
                "coef": self._platt.coef_.ravel().tolist(),
                "intercept": self._platt.intercept_.ravel().tolist(),
            }
        return {
            "method": "isotonic",
            "clip": self.clip,
            "x": np.asarray(self._iso.X_thresholds_).tolist(),
            "y": np.asarray(self._iso.y_thresholds_).tolist(),
        }


def fit_calibrator(
    raw, y, *, method: str = "platt", clip: float = DEFAULT_CLIP
) -> Calibrator:
    """Fit a calibrator on (raw score, win label). Degenerate slice -> identity-with-clip."""
    raw = np.asarray(raw, dtype=float)
    y = np.asarray(y)
    # Feature 039: explicit no-calibration path (cond_logit softmax-only A/B).
    if method in ("none", "identity"):
        return Calibrator(method="identity", clip=clip, identity=True)
    if len(y) < _MIN_CALIB_ROWS or len(np.unique(y)) < 2:
        return Calibrator(method="identity", clip=clip, identity=True)

    raw_c = np.clip(raw, clip, 1.0 - clip)
    if method == "isotonic":
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(raw_c, y)
        return Calibrator(method="isotonic", clip=clip, _iso=iso)

    # Platt: deterministic 1-D logistic regression on the raw score.
    lr = LogisticRegression(solver="lbfgs", C=1e6)
    lr.fit(raw_c.reshape(-1, 1), y)
    return Calibrator(method="platt", clip=clip, _platt=lr)
