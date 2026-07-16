"""Feature 074 US3: OOF-faithful two-gamma re-validation — DB-free tests of the pure driver core.

Covers ece / ks_distance / prequential_held_out (fit fold excluded) / three_way_verdict
(transfer + sufficiency gates first, then ADOPT/REJECT/NO_DECISION).
"""

from __future__ import annotations

from horseracing_probability.oof_calibration import (
    ADOPT,
    NO_DECISION,
    REJECT,
    ece,
    ks_distance,
    prequential_held_out,
    three_way_verdict,
)

GATE = {
    "verdict": {"non_inferior_margin_ece": 0.001, "no_decision_min_days": 10},
    "transfer_check": {"ks_distance_max": 0.10},
}


def test_ece_zero_when_perfectly_calibrated():
    # all p=0.5 with exactly half winners in that bin -> |mean_p - mean_y| = 0
    probs = [0.5] * 10
    labels = [1, 0] * 5
    assert ece(probs, labels) == 0.0


def test_ece_positive_when_miscalibrated():
    probs = [0.9] * 10
    labels = [0] * 10  # predicted 0.9, realized 0.0
    assert ece(probs, labels) > 0.5


def test_ks_distance_zero_for_identical_and_one_for_disjoint():
    assert ks_distance([0.1, 0.2, 0.3], [0.1, 0.2, 0.3]) == 0.0
    assert ks_distance([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]) == 1.0


def test_prequential_excludes_first_fold_and_fits_on_prior_only():
    # 3 folds; first fold is never in the held-out set (no prior to fit on).
    folds = {
        2020: [({"a": 0.6, "b": 0.4}, "a")] * 60,
        2021: [({"a": 0.6, "b": 0.4}, "a")] * 60,
        2022: [({"a": 0.6, "b": 0.4}, "a")] * 60,
    }
    held = prequential_held_out(folds)
    # held-out folds = years after the first = 2
    assert held["n_held_out_folds"] == 2
    # each held-out race contributes 2 horses; 120 races -> 240 pairs
    assert len(held["raw_probs"]) == len(held["cal_probs"]) == len(held["labels"]) == 240
    assert "gamma_lo" in held["last_params"]


def test_verdict_no_decision_when_underpowered():
    v, r = three_way_verdict(0.05, 0.02, ks=0.0, n_days=5, gate_config=GATE)
    assert v == NO_DECISION and r["cause"] == "insufficient_eval_days"


def test_verdict_no_decision_on_transfer_mismatch():
    v, r = three_way_verdict(0.05, 0.02, ks=0.5, n_days=30, gate_config=GATE)
    assert v == NO_DECISION and r["cause"] == "transfer_check_mismatch"


def test_verdict_adopt_when_calibrated_meaningfully_better():
    v, r = three_way_verdict(0.05, 0.02, ks=0.0, n_days=30, gate_config=GATE)
    assert v == ADOPT and r["cause"] == "calibrated_better"


def test_verdict_reject_when_calibrated_worse():
    v, r = three_way_verdict(0.02, 0.05, ks=0.0, n_days=30, gate_config=GATE)
    assert v == REJECT and r["cause"] == "calibrated_worse"


def test_verdict_no_decision_within_margin():
    v, r = three_way_verdict(0.020, 0.0205, ks=0.0, n_days=30, gate_config=GATE)
    assert v == NO_DECISION and r["cause"] == "within_margin"
