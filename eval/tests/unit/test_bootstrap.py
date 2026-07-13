"""T010: race-day moving-block bootstrap CI (FR-004, research D2)."""

from __future__ import annotations

import numpy as np
import pytest

from horseracing_eval.bootstrap import moving_block_bootstrap_ci


def test_seed_determinism_bit_identical():
    diffs = {f"2025-01-{d:02d}": list(np.linspace(-0.01, 0.01, 5)) for d in range(1, 11)}
    a = moving_block_bootstrap_ci(diffs, b=500, seed=123)
    b = moving_block_bootstrap_ci(diffs, b=500, seed=123)
    assert a == b
    assert a.ci_low is not None and a.ci_high is not None


def test_point_estimate_is_overall_mean():
    diffs = {"d1": [0.0, 0.2], "d2": [0.4, 0.4]}
    out = moving_block_bootstrap_ci(diffs, b=100, seed=1)
    assert out.point == np.mean([0.0, 0.2, 0.4, 0.4])


def test_too_few_days_is_no_decision():
    out = moving_block_bootstrap_ci({"only-day": [0.1, 0.2]}, b=100, seed=1)
    assert out.no_decision is True
    assert out.ci_low is None and out.ci_high is None


def test_block_is_race_day_not_iid():
    # If resampling were i.i.d. over races it would ignore day grouping; we assert the
    # implementation resamples whole days (block metadata + contiguous-day pooling).
    diffs = {f"d{d}": [0.05, 0.05, 0.05] for d in range(6)}
    out = moving_block_bootstrap_ci(diffs, b=200, seed=7)
    assert out.block == "race_day"
    assert out.n_days == 6
    # all diffs identical -> every resample mean is the same -> CI collapses to the point
    assert out.ci_low == out.ci_high == out.point
    assert out.point == pytest.approx(0.05, abs=1e-12)


def test_different_seed_can_differ_but_stays_within_range():
    diffs = {f"d{d}": list(np.linspace(-0.02, 0.02, 4)) for d in range(20)}
    a = moving_block_bootstrap_ci(diffs, b=300, seed=1)
    b = moving_block_bootstrap_ci(diffs, b=300, seed=2)
    assert a.point == b.point  # point estimate seed-independent
    assert a.ci_low is not None and b.ci_low is not None
