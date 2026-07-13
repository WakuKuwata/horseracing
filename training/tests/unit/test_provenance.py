"""T030: US3 provenance fields (FR-015) — model_fit_through vs train_through, calib window.

Exercises the split→date logic that predictor.fit records in fit_info_, using the same
race-level chronological split helper, without needing a DB.
"""

from __future__ import annotations

import datetime

from horseracing_training.calibration import split_train_by_time
from horseracing_training.predictor import _max_date, _min_date


def _dates(series):
    return series


def test_model_fit_through_is_before_train_through_with_calib_holdout():
    # 10 races over 10 days; latest 30% become the calib holdout (calib_frac=0.3).
    race_ids = [f"R{i}" for i in range(10)]
    race_dates = {f"R{i}": datetime.date(2025, 1, i + 1) for i in range(10)}
    model_mask, calib_mask = split_train_by_time(race_ids, race_dates, calib_frac=0.3)

    all_dates = [race_dates[r] for r in race_ids]
    model_dates = [race_dates[r] for r, m in zip(race_ids, model_mask, strict=True) if m]
    calib_dates = [race_dates[r] for r, m in zip(race_ids, calib_mask, strict=True) if m]

    train_through = _max_date(all_dates)
    model_fit_through = _max_date(model_dates)
    calib_from = _min_date(calib_dates)
    calib_through = _max_date(calib_dates)

    # booster's last-learned day is strictly before the overall train_through (latest rows held out)
    assert model_fit_through < train_through
    assert train_through == "2025-01-10"
    # calib window is the held-out tail, contiguous and after the model-fit window
    assert calib_through == train_through
    assert calib_from > model_fit_through


def test_no_calib_holdout_leaves_calib_window_none():
    # a single race -> split yields no calib rows -> calib window is None (identity fallback)
    race_ids = ["R0"]
    race_dates = {"R0": datetime.date(2025, 1, 1)}
    _model_mask, calib_mask = split_train_by_time(race_ids, race_dates, calib_frac=0.3)
    calib_dates = [race_dates[r] for r, m in zip(race_ids, calib_mask, strict=True) if m]
    assert calib_dates == []
    assert _min_date(calib_dates) is None
    assert _max_date(calib_dates) is None
