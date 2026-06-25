"""T021 (012): divergence summary — coverage, log(real/est) median/MAE/P90, labels (SC-006)."""

from __future__ import annotations

import math

from horseracing_db.enums import BetType

from horseracing_betting.exotic_divergence import summarize_divergence


def test_coverage_rate_is_pairs_over_estimated():
    rep = summarize_divergence(BetType.TRIO, n_estimated=10, log_ratios=[0.0, 0.0, 0.0])
    assert rep.n_estimated == 10 and rep.n_pairs == 3
    assert rep.coverage_rate == 0.3  # partial coverage explicit, not dropped


def test_log_ratio_stats():
    # real = e^1 * est for all -> log ratio = 1.0 everywhere
    lrs = [1.0, 1.0, 1.0, 1.0]
    rep = summarize_divergence(BetType.EXACTA, n_estimated=4, log_ratios=lrs)
    assert math.isclose(rep.log_ratio_median, 1.0)
    assert math.isclose(rep.log_ratio_mae, 1.0)
    assert math.isclose(rep.log_ratio_p90, 1.0)


def test_signed_median_distinguishes_over_and_under():
    # estimated systematically BELOW real -> positive median log ratio
    rep = summarize_divergence(BetType.WIDE, n_estimated=3, log_ratios=[0.2, 0.5, 0.8])
    assert rep.log_ratio_median == 0.5
    # MAE uses absolute value; mix of signs still positive
    mixed = summarize_divergence(BetType.WIDE, n_estimated=2, log_ratios=[-0.4, 0.4])
    assert math.isclose(mixed.log_ratio_mae, 0.4)
    assert math.isclose(mixed.log_ratio_median, 0.0)


def test_zero_coverage_is_explicit():
    rep = summarize_divergence(BetType.TRIFECTA, n_estimated=50, log_ratios=[])
    assert rep.coverage_rate == 0.0 and rep.n_pairs == 0
    assert rep.log_ratio_mae == 0.0


def test_baseline_labels_double_pseudo():
    rep = summarize_divergence(BetType.PLACE, n_estimated=5, log_ratios=[0.1])
    assert rep.baseline == "estimated(010/011)" and rep.pseudo_baseline is True
