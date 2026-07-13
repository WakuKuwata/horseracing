"""T007: race-level winner NLL PRIMARY + uniform baseline (FR-001/FR-007)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from horseracing_eval.metrics import (
    uniform_baseline_winner_nll,
    winner_nll,
)


def test_winner_nll_is_race_equal_weight_not_per_horse_micro():
    # Two races: winner probs 0.5 and 0.25. Race-equal mean = mean(-log(0.5), -log(0.25)).
    nll, excluded = winner_nll([0.5, 0.25])
    assert excluded == 0
    expected = (-math.log(0.5) - math.log(0.25)) / 2
    assert nll == pytest.approx(expected, abs=1e-12)


def test_winner_nll_excludes_ineligible_and_counts_them():
    # None = dead heat / no winner / unresolved / partial-ingest -> excluded, surfaced.
    nll, excluded = winner_nll([0.4, None, None, 0.6])
    assert excluded == 2
    expected = (-math.log(0.4) - math.log(0.6)) / 2
    assert nll == pytest.approx(expected, abs=1e-12)


def test_winner_nll_all_ineligible_is_nan_with_full_excluded_count():
    nll, excluded = winner_nll([None, None])
    assert excluded == 2
    assert math.isnan(nll)


def test_winner_nll_clips_extremes_no_inf():
    nll, _ = winner_nll([0.0, 1.0])
    assert math.isfinite(nll)


def test_uniform_baseline_is_mean_log_field_size():
    # field sizes 4 and 16 -> uniform win prob 1/N -> -log(1/N) = log(N)
    ub = uniform_baseline_winner_nll([4, 16])
    assert ub == pytest.approx((math.log(4) + math.log(16)) / 2, abs=1e-12)


def test_uniform_baseline_ignores_nonpositive_field_sizes():
    ub = uniform_baseline_winner_nll([0, 8, None])
    assert ub == pytest.approx(math.log(8), abs=1e-12)


def test_winner_nll_deterministic():
    a, _ = winner_nll([0.3, 0.7, 0.1])
    b, _ = winner_nll([0.3, 0.7, 0.1])
    assert a == b
    assert isinstance(a, float)
    assert not isinstance(a, np.floating)
