"""Feature 042 T002: PL top-k objective — stage gradients / break rules / neutralize."""

from __future__ import annotations

import numpy as np

from horseracing_training.cond_logit import (
    _HESS_FLOOR,
    STAGE_WEIGHTS,
    pl_topk_objective,
)


class _DS:
    def __init__(self, weight=None):
        self._w = weight

    def get_label(self):  # unused by pl_topk (ranks come via closure) but part of the API
        return None

    def get_weight(self):
        return self._w


def _softmax(v):
    e = np.exp(v - v.max())
    return e / e.sum()


def test_stage_gradients_match_hand_computation():
    # one group of 4: ranks 1,2,3,0 — three stages fire
    preds = np.array([0.4, 0.2, 0.1, -0.3])
    ranks = np.array([1, 2, 3, 0])
    grad, hess = pl_topk_objective([4], ranks)(preds, _DS())

    g_exp = np.zeros(4)
    h_exp = np.zeros(4)
    remaining = np.ones(4, dtype=bool)
    for j, w in enumerate(STAGE_WEIGHTS, start=1):
        p = _softmax(preds[remaining])
        y = (ranks[remaining] == j).astype(float)
        g_exp[remaining] += w * (p - y)
        h_exp[remaining] += w * np.maximum(p * (1 - p), _HESS_FLOOR)
        remaining = remaining & ~(ranks == j)
    h_exp = np.maximum(h_exp, _HESS_FLOOR)
    assert np.allclose(grad, g_exp)
    assert np.allclose(hess, h_exp)


def test_stage1_equals_cond_logit_component():
    # rank-1 stage alone == cond_logit grad (w=1.0)
    preds = np.array([0.0, 0.0, 0.0])
    ranks = np.array([1, 0, 0])  # no 2nd/3rd info -> only stage1 fires
    grad, _ = pl_topk_objective([3], ranks)(preds, _DS())
    p = np.full(3, 1 / 3)
    y = np.array([1.0, 0.0, 0.0])
    assert np.allclose(grad, p - y)


def test_dead_heat_at_stage2_keeps_stage1():
    # two horses share rank 2 -> stage2 breaks, stage1 gradient kept
    preds = np.array([0.5, 0.1, 0.1, -0.2])
    ranks = np.array([1, 2, 2, 0])
    grad, _ = pl_topk_objective([4], ranks)(preds, _DS())
    p = _softmax(preds)
    y1 = np.array([1.0, 0.0, 0.0, 0.0])
    assert np.allclose(grad, 1.0 * (p - y1))  # only stage1


def test_no_unique_winner_neutralizes_group():
    preds = np.array([0.1, 0.2, 0.3])
    for ranks in (np.array([0, 0, 0]), np.array([1, 1, 2])):
        grad, hess = pl_topk_objective([3], ranks)(preds, _DS())
        assert np.allclose(grad, 0.0)
        assert np.allclose(hess, _HESS_FLOOR)


def test_small_field_breaks_when_remaining_lt_2():
    # 2 horses: stage1 fires; stage2 has remaining==1 -> break
    preds = np.array([0.3, -0.3])
    ranks = np.array([1, 2])
    grad, _ = pl_topk_objective([2], ranks)(preds, _DS())
    p = _softmax(preds)
    y1 = np.array([1.0, 0.0])
    assert np.allclose(grad, p - y1)  # stage2 contributed nothing


def test_sample_weight_scales_grad_hess():
    preds = np.array([0.4, 0.2, 0.1, -0.3])
    ranks = np.array([1, 2, 3, 0])
    g0, h0 = pl_topk_objective([4], ranks)(preds, _DS())
    w = np.array([2.0, 2.0, 2.0, 2.0])
    g1, h1 = pl_topk_objective([4], ranks)(preds, _DS(weight=w))
    assert np.allclose(g1, 2.0 * g0)
    assert np.allclose(h1, 2.0 * h0)


def test_multiple_groups_isolated():
    # two groups: gradients computed independently per group
    preds = np.array([0.4, -0.4, 0.2, 0.1, -0.1])
    ranks = np.array([1, 0, 1, 2, 0])
    grad, _ = pl_topk_objective([2, 3], ranks)(preds, _DS())
    g1, _ = pl_topk_objective([2], ranks[:2])(preds[:2], _DS())
    g2, _ = pl_topk_objective([3], ranks[2:])(preds[2:], _DS())
    assert np.allclose(grad, np.concatenate([g1, g2]))
