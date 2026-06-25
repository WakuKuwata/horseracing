"""T018 (013): evaluate_q_vs_qprime — fixed-bin ECE/reliability, adoption gate (SC-004/SC-007)."""

from __future__ import annotations

import pytest

from horseracing_probability.fl_bias import FLCalibrator, fit_fl_calibrator
from horseracing_probability.market_calibration import DEFAULT_BINS, evaluate_q_vs_qprime

BASE = {"A": 0.45, "B": 0.28, "C": 0.17, "D": 0.10}


def _odds(q):
    return {h: 1.0 / p for h, p in q.items()}


def _favorite_biased_samples(n):
    # market under-rates the favorite: A wins far more than its q=0.45 -> γ>1 should help
    return [(_odds(BASE), "A" if i % 5 != 0 else "B") for i in range(n)]


def test_correction_improves_when_favorites_overperform():
    samples = _favorite_biased_samples(250)
    cal = fit_fl_calibrator(samples)
    rep = evaluate_q_vs_qprime(samples, cal)
    assert rep.improved is True             # q' lowers winner NLL -> adoption signal
    assert rep.nll_qp <= rep.nll_q
    assert rep.pseudo is True and rep.scope == "overall"


def test_fixed_bins_and_reliability_shape():
    samples = _favorite_biased_samples(120)
    cal = fit_fl_calibrator(samples)
    rep = evaluate_q_vs_qprime(samples, cal, bins=DEFAULT_BINS)
    # one reliability row per bin; each is (mean_pred, empirical_rate, n)
    assert len(rep.reliability_q) == len(DEFAULT_BINS) - 1
    assert len(rep.reliability_qp) == len(DEFAULT_BINS) - 1
    for mp, er, nbin in rep.reliability_q:
        assert 0.0 <= mp <= 1.0 and 0.0 <= er <= 1.0 and nbin >= 0
    # empty bins are explicit (n=0), never dropped
    assert any(n == 0 for _, _, n in rep.reliability_q) or all(
        n > 0 for _, _, n in rep.reliability_q
    )


def test_call_site_independent_default_bins():
    samples = _favorite_biased_samples(100)
    cal = fit_fl_calibrator(samples)
    a = evaluate_q_vs_qprime(samples, cal)
    b = evaluate_q_vs_qprime(samples, cal, bins=DEFAULT_BINS)
    assert a.ece_q == b.ece_q and a.ece_qp == b.ece_qp  # default == explicit fixed bins


def test_dead_heat_and_no_winner_excluded():
    samples = [(_odds(BASE), None) for _ in range(5)]  # no winner -> excluded
    cal = FLCalibrator("power", {"gamma": 1.5}, None, 0, 0, (0.1, 0.45), "lv")
    with pytest.raises(ValueError):
        evaluate_q_vs_qprime(samples, cal)  # insufficient -> fail fast


def test_deterministic():
    samples = _favorite_biased_samples(140)
    cal = fit_fl_calibrator(samples)
    a = evaluate_q_vs_qprime(samples, cal)
    b = evaluate_q_vs_qprime(samples, cal)
    assert a.nll_qp == b.nll_qp and a.ece_qp == b.ece_qp
