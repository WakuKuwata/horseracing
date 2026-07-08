"""Vectorized pl_topk_objective == per-group loop oracle (allclose; ~1 ulp accepted)."""

from __future__ import annotations

import numpy as np

from horseracing_training.cond_logit import (
    _HESS_FLOOR,
    _pl_topk_objective_loop,
    pl_topk_objective,
)


class _DS:
    def __init__(self, weight=None):
        self._w = weight

    def get_label(self):
        return None

    def get_weight(self):
        return self._w


def _assert_equiv(group_sizes, ranks, preds, weight=None):
    ds = _DS(weight)
    gv, hv = pl_topk_objective(group_sizes, ranks)(preds, ds)
    gl, hl = _pl_topk_objective_loop(group_sizes, ranks)(preds, ds)
    # ~1 ulp on the softmax denominator is accepted (user decision); catch real bugs, not noise
    np.testing.assert_allclose(gv, gl, rtol=0, atol=1e-12)
    np.testing.assert_allclose(hv, hl, rtol=0, atol=1e-12)


def test_hand_cases_match_loop():
    # normal 4-horse (3 stages), stage-2 dead heat (break), no-winner (skip), size-1, size-2
    cases = [
        ([4], [1, 2, 3, 0], [0.4, 0.2, 0.1, -0.3]),
        ([4], [1, 2, 2, 0], [0.5, 0.1, 0.1, -0.2]),   # dead heat 2nd -> stage2 break
        ([3], [0, 0, 0], [0.1, 0.2, 0.3]),            # no winner -> neutralized
        ([3], [1, 1, 2], [0.1, 0.2, 0.3]),            # two winners -> neutralized
        ([1], [1], [0.7]),                            # size 1 -> stage1 breaks (remaining<2)
        ([2], [1, 2], [0.3, -0.3]),                   # size 2 -> only stage1
        ([5], [1, 2, 3, 0, 0], [0.9, 0.3, 0.1, 0.0, -0.5]),
    ]
    for gs, rk, pr in cases:
        _assert_equiv(gs, np.array(rk), np.array(pr, dtype=float))


def test_multiple_groups_and_weight():
    group_sizes = [2, 3, 4, 1, 5]
    ranks = np.array([1, 0,  1, 2, 0,  1, 2, 3, 0,  1,  1, 2, 0, 3, 0])
    rng = np.random.default_rng(0)
    preds = rng.normal(size=len(ranks))
    _assert_equiv(group_sizes, ranks, preds)
    _assert_equiv(group_sizes, ranks, preds, weight=rng.uniform(0.5, 2.0, size=len(ranks)))


def test_random_fuzz_matches_loop():
    rng = np.random.default_rng(42)
    for _ in range(200):
        n_groups = rng.integers(1, 12)
        sizes = rng.integers(1, 16, size=n_groups)
        ranks = []
        for g in sizes:
            r = np.zeros(g, dtype=int)
            # randomly assign a unique winner (sometimes not, to hit the skip path) + maybe 2nd/3rd
            if rng.random() < 0.85 and g >= 1:
                order = rng.permutation(g)
                k = min(3, g)
                for pos in range(k):
                    if rng.random() < 0.9:
                        r[order[pos]] = pos + 1
                # occasionally inject a dead heat
                if g >= 3 and rng.random() < 0.15:
                    r[order[min(1, g - 1)]] = r[order[min(2, g - 1)]] = 2
            ranks.append(r)
        ranks = np.concatenate(ranks)
        preds = rng.normal(scale=3.0, size=int(sizes.sum()))
        _assert_equiv(list(sizes), ranks, preds)


def test_extreme_logits_floor_and_underflow():
    # large-magnitude margins: exp underflow + per-stage floor timing must match the loop exactly
    _assert_equiv([4], np.array([1, 2, 3, 0]), np.array([100.0, 90.0, 80.0, -100.0]))
    _assert_equiv([4], np.array([1, 2, 3, 0]), np.array([-50.0, -50.0, -50.0, -50.0]))


def test_hess_never_below_floor():
    gv, hv = pl_topk_objective([3, 4], np.array([1, 2, 0, 1, 2, 3, 0]))(
        np.zeros(7), _DS()
    )
    assert (hv >= _HESS_FLOOR - 1e-18).all()
