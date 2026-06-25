"""T010 (013): fit_fl_calibrator -> apply_calibrator, logic_version, method error (SC-001/SC-006)."""

from __future__ import annotations

import datetime
import math

import pytest

from horseracing_probability.fl_bias import apply_calibrator, fit_fl_calibrator

BASE = {"A": 0.45, "B": 0.28, "C": 0.17, "D": 0.10}


def _odds(q):
    return {h: 1.0 / p for h, p in q.items()}


def _samples(n):
    # favorites over-perform -> informative for gamma
    return [(_odds(BASE), "A" if i % 4 != 0 else "D") for i in range(n)]


def test_fit_apply_power_sum_to_one_and_monotone():
    cal = fit_fl_calibrator(_samples(150), method="power",
                            train_window=(datetime.date(2007, 1, 1), datetime.date(2007, 12, 31)))
    assert cal.method == "power" and "gamma" in cal.params
    cp = apply_calibrator(cal, _odds(BASE))
    assert math.isclose(sum(cp.q_prime.values()), 1.0, abs_tol=1e-9)
    order_q = sorted(cp.q, key=cp.q.get)
    order_qp = sorted(cp.q_prime, key=cp.q_prime.get)
    assert order_q == order_qp
    assert cp.field_size == 4


def test_logic_version_records_method_gamma_window_samples():
    cal = fit_fl_calibrator(_samples(120), train_window=("2007-01-01", "2007-12-31"))
    lv = cal.logic_version
    assert "fl=power(q^gamma)" in lv and "gamma=" in lv
    assert "window=" in lv and "n_info=" in lv
    assert cal.n_samples > 0 and cal.sufficient is True


def test_isotonic_and_loglog_not_implemented():
    with pytest.raises(NotImplementedError):
        fit_fl_calibrator(_samples(10), method="isotonic")
    with pytest.raises(NotImplementedError):
        fit_fl_calibrator(_samples(10), method="loglog")


def test_out_of_range_counted():
    cal = fit_fl_calibrator(_samples(120))
    # an extreme longshot whose q is below the trained range
    cp = apply_calibrator(cal, {"A": 1.5, "B": 3.0, "C": 4.0, "Z": 5000.0})
    assert cp.out_of_range >= 1  # extrapolation audit surfaced


def test_insufficient_data_identity_fallback():
    # flat q only -> no informative races -> gamma=1 identity, sufficient False
    flat = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    cal = fit_fl_calibrator([(_odds(flat), "A") for _ in range(5)])
    assert cal.params["gamma"] == 1.0 and cal.sufficient is False


def test_deterministic():
    a = fit_fl_calibrator(_samples(100))
    b = fit_fl_calibrator(_samples(100))
    assert a.params == b.params
