"""Feature 048: two_gamma asymmetric calibration — continuity at the pivot, monotonicity,
Σ=1, determinism, degeneracy (γlo=γhi == uniform power), identity fallback, power unchanged.
"""

from __future__ import annotations

import math

import pytest

from horseracing_probability.model_calibration import (
    TWO_GAMMA_PIVOT,
    PCalibrator,
    _apply_gamma,
    _apply_two_gamma,
    _two_gamma_weight,
    apply_p_calibrator,
    fit_p_calibrator,
    fit_two_gamma,
)


def _cal(glo: float, ghi: float) -> PCalibrator:
    return PCalibrator(
        method="two_gamma", params={"gamma_lo": glo, "gamma_hi": ghi, "pivot": TWO_GAMMA_PIVOT},
        train_window=None, n_races=100, n_samples=90, prob_range=(0.0, 1.0),
        select="mle", base_model_version=None, logic_version="pcal=two_gamma;test",
        sufficient=True,
    )


def test_weight_continuous_and_monotone_at_pivot():
    glo, ghi, piv = 2.0, 0.7, TWO_GAMMA_PIVOT
    below = _two_gamma_weight(piv - 1e-9, glo, ghi, piv)
    at = _two_gamma_weight(piv, glo, ghi, piv)
    above = _two_gamma_weight(piv + 1e-9, glo, ghi, piv)
    assert math.isclose(below, at, rel_tol=1e-6) and math.isclose(at, above, rel_tol=1e-6)
    # monotone over a sweep
    xs = [i / 1000 for i in range(1, 1000)]
    ws = [_two_gamma_weight(x, glo, ghi, piv) for x in xs]
    assert all(a < b for a, b in zip(ws[:-1], ws[1:], strict=True))


def test_apply_normalizes_and_is_deterministic():
    p = {"a": 0.5, "b": 0.3, "c": 0.15, "d": 0.05}
    out1 = _apply_two_gamma(p, 1.8, 1.1)
    out2 = _apply_two_gamma(p, 1.8, 1.1)
    assert out1 == out2
    assert math.isclose(sum(out1.values()), 1.0, rel_tol=1e-9)


def test_degenerates_to_uniform_power_when_gammas_equal():
    p = {"a": 0.5, "b": 0.3, "c": 0.15, "d": 0.05}
    two = _apply_two_gamma(p, 1.34, 1.34)
    uni = _apply_gamma(p, 1.34)
    for h in p:
        assert math.isclose(two[h], uni[h], rel_tol=1e-9), h


def test_apply_p_calibrator_dispatches_two_gamma():
    p = {"a": 0.6, "b": 0.3, "c": 0.1}
    out = apply_p_calibrator(p, _cal(2.0, 0.8))
    assert math.isclose(sum(out.values()), 1.0, rel_tol=1e-9)
    assert out != p  # actually transformed


def _synth_samples(n_races=120, sharp=True):
    # favorite (p=0.4) wins often when sharp — informative for gamma fitting
    samples = []
    for i in range(n_races):
        p = {"F": 0.4, "M": 0.35, "L": 0.25}
        winner = "F" if (i % 10) < 6 else ("M" if (i % 10) < 9 else "L")
        samples.append((p, winner))
    return samples


def test_fit_two_gamma_deterministic_and_fallback():
    s = _synth_samples()
    a = fit_two_gamma(s)
    b = fit_two_gamma(s)
    assert a == b                       # deterministic
    assert fit_two_gamma([]) == (1.0, 1.0, 0)  # no data -> neutral


def test_fit_p_calibrator_two_gamma_and_identity_fallback():
    cal = fit_p_calibrator(_synth_samples(), method="two_gamma")
    assert cal.method == "two_gamma" and cal.sufficient
    assert "pcal=two_gamma" in cal.logic_version and "pivot=" in cal.logic_version
    # thin data -> identity fallback (min_races=50 default)
    thin = fit_p_calibrator(_synth_samples(n_races=10), method="two_gamma")
    assert thin.method == "identity" and thin.sufficient is False
    # unknown method still raises
    with pytest.raises(NotImplementedError):
        fit_p_calibrator(_synth_samples(), method="isotonic")


def test_power_lv_format_unchanged():
    cal = fit_p_calibrator(_synth_samples(), method="power")
    assert cal.method == "power"
    assert "pcal=power(p^gamma);gamma=" in cal.logic_version  # pre-048 byte format kept
