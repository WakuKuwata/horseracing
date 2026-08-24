import numpy as np
import pytest

from horseracing_training.cond_logit import (
    _HESS_FLOOR,
    _pl_topk_objective_loop,
    pl_topk_objective,
)

GROUP_SIZES = [8, 5, 12, 3]
N_GROUPS = len(GROUP_SIZES)
K = 3


class FakeDataset:
    def __init__(self, ranks, weight=None):
        self._label = (np.asarray(ranks) == 1).astype(float)
        self._weight = weight

    def get_label(self):
        return self._label

    def get_weight(self):
        return self._weight


def _inputs():
    rng = np.random.default_rng(42)
    ranks = np.zeros(sum(GROUP_SIZES), dtype=int)
    start = 0
    for group_size in GROUP_SIZES:
        order = rng.permutation(group_size)
        ranks[start + order[:K]] = np.arange(1, K + 1)
        start += group_size
    preds = rng.normal(size=len(ranks))
    offsets = rng.normal(scale=0.25, size=len(ranks))
    weights = rng.uniform(0.25, 2.0, size=len(ranks))
    return ranks, preds, offsets, weights


@pytest.mark.parametrize("use_offsets", [False, True])
@pytest.mark.parametrize("use_weights", [False, True])
def test_none_is_bit_identical_to_all_ones(use_offsets, use_weights):
    ranks, preds, finite_offsets, positive_weights = _inputs()
    offsets = finite_offsets if use_offsets else None
    weights = positive_weights if use_weights else None
    dataset = FakeDataset(ranks, weights)

    baseline = pl_topk_objective(GROUP_SIZES, ranks, offsets=offsets)(preds, dataset)
    ones = pl_topk_objective(
        GROUP_SIZES,
        ranks,
        offsets=offsets,
        stage_scales=np.ones((N_GROUPS, K)),
    )(preds, dataset)

    assert np.array_equal(baseline[0], ones[0])
    assert np.array_equal(baseline[1], ones[1])


def test_uniform_half_scales_grad_and_hess():
    ranks, preds, _, _ = _inputs()
    dataset = FakeDataset(ranks)
    baseline = pl_topk_objective(GROUP_SIZES, ranks)(preds, dataset)
    scaled = pl_topk_objective(
        GROUP_SIZES,
        ranks,
        stage_scales=np.full((N_GROUPS, K), 0.5),
    )(preds, dataset)

    assert np.allclose(scaled[0], 0.5 * baseline[0], rtol=1e-12, atol=0.0)
    assert np.allclose(scaled[1], 0.5 * baseline[1], rtol=1e-12, atol=0.0)


def test_zero_stage_two_scale_changes_gradients():
    ranks, preds, _, _ = _inputs()
    dataset = FakeDataset(ranks)
    baseline_grad, _ = pl_topk_objective(GROUP_SIZES, ranks)(preds, dataset)
    scales = np.ones((N_GROUPS, K))
    scales[:, 1] = 0.0
    scaled_grad, _ = pl_topk_objective(
        GROUP_SIZES,
        ranks,
        stage_scales=scales,
    )(preds, dataset)

    assert not np.array_equal(scaled_grad, baseline_grad)


def test_non_unique_winner_group_stays_neutralized_with_scales():
    ranks, preds, _, _ = _inputs()
    group_start = GROUP_SIZES[0]
    group_stop = group_start + GROUP_SIZES[1]
    ranks[group_start:group_stop] = 0
    ranks[group_start : group_start + 2] = 1
    dataset = FakeDataset(ranks)

    grad, hess = pl_topk_objective(
        GROUP_SIZES,
        ranks,
        stage_scales=np.full((N_GROUPS, K), 0.5),
    )(preds, dataset)

    assert np.array_equal(grad[group_start:group_stop], np.zeros(GROUP_SIZES[1]))
    assert np.array_equal(
        hess[group_start:group_stop],
        np.full(GROUP_SIZES[1], _HESS_FLOOR),
    )


def test_vectorized_matches_loop_with_random_scales():
    ranks, preds, offsets, weights = _inputs()
    rng = np.random.default_rng(20260824)
    scales = rng.uniform(0.25, 1.0, size=(N_GROUPS, K))
    dataset = FakeDataset(ranks, weights)

    vectorized = pl_topk_objective(
        GROUP_SIZES,
        ranks,
        offsets=offsets,
        stage_scales=scales,
    )(preds, dataset)
    loop = _pl_topk_objective_loop(
        GROUP_SIZES,
        ranks,
        offsets=offsets,
        stage_scales=scales,
    )(preds, dataset)

    assert np.allclose(vectorized[0], loop[0])
    assert np.allclose(vectorized[1], loop[1])


@pytest.mark.parametrize("factory", [pl_topk_objective, _pl_topk_objective_loop])
def test_wrong_stage_scales_shape_raises_value_error(factory):
    ranks, _, _, _ = _inputs()

    with pytest.raises(ValueError):
        factory(GROUP_SIZES, ranks, stage_scales=np.ones((N_GROUPS, K - 1)))
