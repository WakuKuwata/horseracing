"""US2 (FR-009): independent-product baseline differs from PL; calibration scoring (DB-free)."""

from __future__ import annotations

import pytest

from horseracing_probability.calibration import calibrate, independent_product


def test_independent_product_differs_and_normalizes():
    win = {"A": 0.5, "B": 0.3, "C": 0.2}
    ind = independent_product(win, 2)
    assert sum(ind.values()) == pytest.approx(1.0, abs=1e-9)
    # independent exacta(A,B) ∝ 0.5*0.3 / Z (Z = 1 - Σp^2 = 0.62) = 0.241935, != PL's 0.30
    assert ind[("A", "B")] == pytest.approx(0.15 / 0.62, abs=1e-6)
    assert ind[("A", "B")] != pytest.approx(0.30, abs=1e-3)


def test_calibrate_pl_not_worse_when_realized_is_pl_favored():
    # realized = favorite-ordered (A then B): PL assigns more mass (0.30) than independent (0.242)
    samples = [({"A": 0.5, "B": 0.3, "C": 0.2}, ("A", "B"))]
    rep = calibrate(samples, bet_type="exacta")
    assert rep["plackett_luce"].n_races == 1
    assert rep["plackett_luce"].nll < rep["independent_product"].nll  # PL better calibrated here


def test_calibrate_trifecta_runs():
    samples = [({"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}, ("A", "B", "C"))]
    rep = calibrate(samples, bet_type="trifecta")
    assert set(rep) == {"plackett_luce", "independent_product"}
    assert rep["plackett_luce"].brier >= 0
