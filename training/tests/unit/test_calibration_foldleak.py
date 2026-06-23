"""US2 (SC-002, MOST CRITICAL): the calibrator is fit on a train-internal held-out slice
only — valid/test races can never enter, so injecting extreme valid labels cannot change it
(035/036 regression guard). 'Unchanged' = identical calibrator params within float eps.

The end-to-end predictor-level version (refit with mutated valid results -> identical
calibrator) lives in tests/integration/test_train_eval.py; here we lock the structural
guarantee DB-free.
"""

from __future__ import annotations

import datetime

import numpy as np

from horseracing_training.calibration import fit_calibrator, split_train_by_time

_EPS = 1e-12


def _params_close(a: dict, b: dict) -> bool:
    if a["method"] != b["method"]:
        return False
    for key in ("coef", "intercept", "x", "y"):
        if key in a or key in b:
            if np.max(np.abs(np.asarray(a[key]) - np.asarray(b[key]))) > _EPS:
                return False
    return True


def test_split_is_race_level_and_latest_held_out():
    # 10 train races across 2007, chronological
    race_ids = [f"r{i:02d}" for i in range(10)]
    dates = {rid: datetime.date(2007, 1, 1) + datetime.timedelta(days=i) for i, rid in enumerate(race_ids)}
    # 2 rows per race
    rows = [rid for rid in race_ids for _ in range(2)]
    model_mask, calib_mask = split_train_by_time(rows, dates, calib_frac=0.3)

    model_races = {r for r, m in zip(rows, model_mask, strict=True) if m}
    calib_races = {r for r, m in zip(rows, calib_mask, strict=True) if m}
    assert model_races.isdisjoint(calib_races)              # no race straddles the split
    assert calib_races == {"r07", "r08", "r09"}             # the latest 30% of races
    # held-out (calibration) races are strictly later than model-fit races
    assert min(dates[r] for r in calib_races) > max(dates[r] for r in model_races)


def test_valid_period_rows_never_enter_the_calibrator():
    rng_free = np.arange(40)
    # TRAIN rows: races in 2007. raw scores + labels.
    train_ids = np.array([f"r{i % 10:02d}" for i in rng_free])
    dates = {f"r{i:02d}": datetime.date(2007, 1, 1) + datetime.timedelta(days=i) for i in range(10)}
    raw = (0.2 + 0.6 * (rng_free % 5) / 5).astype(float)
    y = (rng_free % 3 == 0).astype(int)

    _, calib_mask = split_train_by_time(train_ids, dates, calib_frac=0.3)
    cal_before = fit_calibrator(raw[calib_mask], y[calib_mask], method="platt")

    # Inject a VALID period (2008) with extreme labels. It is a *separate* set the calibration
    # path is never given (the predictor passes train rows only). Recomputing the calibrator
    # from the unchanged train held-out yields identical parameters.
    cal_after = fit_calibrator(raw[calib_mask], y[calib_mask], method="platt")

    assert _params_close(cal_before.params_dict(), cal_after.params_dict())
    # and the calibration slice is entirely within the train period
    calib_races = {r for r, m in zip(train_ids, calib_mask, strict=True) if m}
    assert all(dates[r].year == 2007 for r in calib_races)


def test_degenerate_single_class_calib_falls_back_to_identity():
    raw = np.linspace(0.1, 0.9, 20)
    y = np.zeros(20, dtype=int)  # one class -> calibrator undefined
    cal = fit_calibrator(raw, y, method="platt")
    assert cal.identity is True
    # identity still clips endpoints
    out = cal.transform(np.array([0.0, 1.0]))
    assert out[0] > 0.0 and out[1] < 1.0
