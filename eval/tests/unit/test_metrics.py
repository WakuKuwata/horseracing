"""US1 (SC-001): metrics match hand-computed values on synthetic data."""

from __future__ import annotations

import math

from horseracing_eval.metrics import (
    auc_label,
    brier_label,
    ece_label,
    log_loss_label,
    ndcg_label,
)


def test_log_loss_known():
    # 0.5/0.5 predictions -> -ln(0.5) = 0.6931
    assert math.isclose(log_loss_label([0.5, 0.5], [1, 0]), math.log(2), rel_tol=1e-6)


def test_brier_known():
    assert math.isclose(brier_label([0.5, 0.5], [1, 0]), 0.25, rel_tol=1e-9)


def test_auc_known_and_single_class():
    assert math.isclose(auc_label([0.9, 0.1], [1, 0]), 1.0, rel_tol=1e-9)
    assert auc_label([0.9, 0.1], [1, 1]) is None  # single class -> undefined


def test_ece_perfectly_calibrated_is_zero():
    # half the 0.5-confidence predictions are positive -> calibrated -> ECE 0
    assert math.isclose(ece_label([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0], bins=10), 0.0, abs_tol=1e-9)


def test_ece_miscalibrated_positive():
    # all predict 0.9 but none positive -> ECE 0.9
    assert math.isclose(ece_label([0.9, 0.9], [0, 0], bins=10), 0.9, rel_tol=1e-9)


def test_ndcg_ranks_winner_first():
    # one race, 3 horses; winner has the highest score -> NDCG 1.0
    score = ndcg_label([0.7, 0.2, 0.1], [1, 0, 0], ["R", "R", "R"])
    assert math.isclose(score, 1.0, rel_tol=1e-9)
