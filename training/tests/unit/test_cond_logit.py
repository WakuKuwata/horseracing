"""Feature 039 US1: cond_logit objective helpers (softmax / grad-hess / group)."""

from __future__ import annotations

import numpy as np

from horseracing_training.cond_logit import (
    cond_logit_objective,
    group_sizes_from_race_ids,
    race_softmax,
    winner_nll,
)


def test_group_sizes_contiguous_runs():
    # INV-O4: contiguous run lengths in encounter order
    rids = np.array(["A", "A", "A", "B", "B", "C"])
    assert group_sizes_from_race_ids(rids) == [3, 2, 1]
    assert group_sizes_from_race_ids(np.array([])) == []


def test_race_softmax_sums_to_one_per_group():
    # INV-O1: each group sums to 1, numerically stable with large scores
    scores = np.array([1000.0, 1000.0, 999.0, 0.0, 0.0])  # group1 of 3, group2 of 2
    p = race_softmax(scores, [3, 2])
    assert abs(p[:3].sum() - 1.0) < 1e-12
    assert abs(p[3:].sum() - 1.0) < 1e-12
    assert np.all(np.isfinite(p))
    assert p[3] == p[4]  # equal scores -> equal prob


def test_objective_grad_hess_one_winner():
    # INV-O2: grad = p - y, hess = max(p(1-p), floor) for a one-winner group
    preds = np.array([0.0, 0.0, 0.0])
    y = np.array([1.0, 0.0, 0.0])

    class _DS:
        def get_label(self):
            return y

    grad, hess = cond_logit_objective([3])(preds, _DS())
    p = np.full(3, 1.0 / 3)
    assert np.allclose(grad, p - y)
    assert np.allclose(hess, np.maximum(p * (1 - p), 1e-6))


def test_objective_neutralizes_malformed_groups():
    # INV-O3: no-winner (sum y=0) and dead-heat (sum y=2) groups -> grad 0, hess floor
    preds = np.array([0.1, 0.2, 0.3, 0.4])
    y = np.array([0.0, 0.0, 1.0, 1.0])  # one group of 4 with TWO winners (dead heat)

    class _DS:
        def get_label(self):
            return y

    grad, hess = cond_logit_objective([4])(preds, _DS())
    assert np.allclose(grad, 0.0)
    assert np.allclose(hess, 1e-6)


def test_cond_logit_source_reads_no_result_or_odds():
    # INV-L3: the objective module never references result-time / odds columns
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "horseracing_training" / "cond_logit.py"
    text = src.read_text(encoding="utf-8").lower()
    for tok in ("finish_order", "result_status", "odds", "payout", "dividend", "popularity"):
        assert tok not in text, tok


def test_winner_nll_one_winner_races_only():
    # unsorted input; only the one-winner race contributes
    probs = np.array([0.5, 0.5, 0.7, 0.3])
    y = np.array([1.0, 0.0, 0.0, 0.0])  # race A has winner idx0; race B no winner
    rids = np.array(["A", "A", "B", "B"])
    nll, n = winner_nll(probs, y, rids)
    assert n == 1
    assert abs(nll - (-np.log(0.5))) < 1e-9
