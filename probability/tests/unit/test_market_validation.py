"""US2 (FR-009): evaluate_from_samples — recovery error 0 when R·S=1, q calibration (DB-free)."""

from __future__ import annotations

import pytest

from horseracing_probability.market_calibration import evaluate_from_samples


def test_recovery_zero_when_rs_equals_one():
    # odds=R/s with R_win=0.8 -> Σ1/odds=1.25, R·S=1 -> zero recovery error
    samples = [({"A": 1.6, "B": 3.2, "C": 3.2}, "A")]
    rec, qcal = evaluate_from_samples(samples, payout_rate_win=0.80)
    assert rec.n_races == 1
    assert rec.mean_abs_log_ratio == pytest.approx(0.0, abs=1e-9)
    assert rec.mean_abs_rel_error == pytest.approx(0.0, abs=1e-9)
    assert rec.pseudo is True


def test_q_calibration_scored_and_pseudo():
    samples = [
        ({"A": 1.6, "B": 3.2, "C": 3.2}, "A"),   # market favorite won
        ({"A": 2.0, "B": 4.0, "C": 4.0}, "B"),
    ]
    _, qcal = evaluate_from_samples(samples, payout_rate_win=0.80)
    assert qcal.n_races == 2
    assert qcal.nll > 0 and qcal.brier >= 0
    assert qcal.pseudo is True


def test_dead_heat_winner_none_skips_q():
    samples = [({"A": 1.6, "B": 3.2, "C": 3.2}, None)]  # no single winner
    rec, qcal = evaluate_from_samples(samples, payout_rate_win=0.80)
    assert rec.n_races == 1 and qcal.n_races == 0  # recovery still counts; q skipped
