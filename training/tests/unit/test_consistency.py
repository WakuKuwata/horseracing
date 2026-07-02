"""US1 (SC-001): assemble_predictions yields consistency-passing output, even at endpoints."""

from __future__ import annotations

import numpy as np
import pytest
from horseracing_eval.consistency import check_consistency
from horseracing_eval.stage_discount import StageDiscount

from horseracing_training.predictor import assemble_predictions


def _ids(n: int) -> list[str]:
    return [f"H{i}" for i in range(n)]


def test_normal_field_is_consistent():
    cal = np.array([0.4, 0.2, 0.15, 0.15, 0.1])
    preds = assemble_predictions(_ids(5), cal)
    check_consistency(preds)  # must not raise
    assert abs(sum(p.win for p in preds.values()) - 1.0) < 1e-9


def test_endpoint_scores_are_clipped_and_consistent():
    # raw calibrated scores at the extremes (0 and 1) must be clipped so top3 stays defined
    cal = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    preds = assemble_predictions(_ids(6), cal)
    check_consistency(preds)
    for p in preds.values():
        assert 0.0 <= p.win <= p.top2 <= p.top3 <= 1.0


def test_small_field_below_k():
    # N=2 < 3: every horse is trivially in top3; target sums are min(k, N)
    preds = assemble_predictions(_ids(2), np.array([0.7, 0.3]))
    check_consistency(preds)


@pytest.mark.parametrize("n", [3, 5, 8, 12, 18])
def test_uniform_field_various_sizes(n):
    preds = assemble_predictions(_ids(n), np.full(n, 1.0 / n))
    check_consistency(preds)


# ---- Feature 049: stage_discount pass-through (INV-S2/S9) --------------------


def test_stage_discount_none_byte_identical():
    cal = np.array([0.4, 0.2, 0.15, 0.15, 0.1])
    base = assemble_predictions(_ids(5), cal)
    ident = assemble_predictions(_ids(5), cal, stage_discount=StageDiscount())
    for h in base:
        assert base[h].win == ident[h].win
        assert base[h].top2 == ident[h].top2   # identity == legacy, exact
        assert base[h].top3 == ident[h].top3


def test_stage_discount_leaves_win_unchanged_changes_tail():
    cal = np.array([0.45, 0.25, 0.15, 0.08, 0.04, 0.03])
    base = assemble_predictions(_ids(6), cal)
    disc = assemble_predictions(_ids(6), cal, stage_discount=StageDiscount(lambda2=0.5, lambda3=0.5))
    check_consistency(disc)
    for h in base:
        assert disc[h].win == base[h].win           # INV-S2: win untouched
    # favourite's tail is compressed by the discount
    assert disc["H0"].top2 < base["H0"].top2
    assert disc["H0"].top3 < base["H0"].top3
