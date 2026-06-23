"""US1 (SC-001): assemble_predictions yields consistency-passing output, even at endpoints."""

from __future__ import annotations

import numpy as np
import pytest
from horseracing_eval.consistency import check_consistency

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
