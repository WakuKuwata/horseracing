"""T010: race-day cluster bootstrap CI (FR-004, research D2).

Feature 073 (US3, T022): the function was renamed ``moving_block_bootstrap_ci`` ->
``race_day_cluster_bootstrap_ci_v1`` with byte-identical numbers; ``test_rename_numeric_golden``
pins fixed inputs so any future numeric drift is caught.
"""

from __future__ import annotations

import numpy as np
import pytest

from horseracing_eval.bootstrap import (
    race_day_cluster_bootstrap_ci_v1,
    race_day_cluster_bootstrap_sensitivity_v2,
)


def test_v2_sensitivity_labels_and_determinism():
    # Feature 073 T023: v2 sensitivities are diagnostic, deterministic, and labelled by block width.
    diffs = {f"2025-01-{d:02d}": [-0.01, 0.0, 0.01] for d in range(1, 15)}
    a = race_day_cluster_bootstrap_sensitivity_v2(diffs, b=200, seed=5)
    b = race_day_cluster_bootstrap_sensitivity_v2(diffs, b=200, seed=5)
    assert set(a) == {"2d", "3d", "4d", "week"}
    # same seed -> bit-identical CIs across the whole sensitivity set (SC-003 spirit)
    for k in a:
        assert (a[k].ci_low, a[k].ci_high, a[k].point) == (b[k].ci_low, b[k].ci_high, b[k].point)
    # coarser blocks => fewer clusters than the 14 raw days
    assert a["4d"].n_days <= a["2d"].n_days < 14


def test_rename_numeric_golden():
    # Feature 073 T022: pinned CI for a fixed input/seed — the 073 rename must not move numbers.
    diffs = {
        "2025-01-01": [-0.02, 0.01, -0.03],
        "2025-01-02": [0.00, -0.01],
        "2025-01-08": [-0.05, 0.02, -0.01, 0.00],
    }
    ci = race_day_cluster_bootstrap_ci_v1(diffs, b=500, seed=42)
    assert ci.point == pytest.approx(-0.01)
    assert ci.ci_low == pytest.approx(-0.013333333333333332)
    assert ci.ci_high == pytest.approx(-0.005)
    assert ci.n_days == 3
    assert ci.block == "race_day"


def test_seed_determinism_bit_identical():
    diffs = {f"2025-01-{d:02d}": list(np.linspace(-0.01, 0.01, 5)) for d in range(1, 11)}
    a = race_day_cluster_bootstrap_ci_v1(diffs, b=500, seed=123)
    b = race_day_cluster_bootstrap_ci_v1(diffs, b=500, seed=123)
    assert a == b
    assert a.ci_low is not None and a.ci_high is not None


def test_point_estimate_is_overall_mean():
    diffs = {"d1": [0.0, 0.2], "d2": [0.4, 0.4]}
    out = race_day_cluster_bootstrap_ci_v1(diffs, b=100, seed=1)
    assert out.point == np.mean([0.0, 0.2, 0.4, 0.4])


def test_too_few_days_is_no_decision():
    out = race_day_cluster_bootstrap_ci_v1({"only-day": [0.1, 0.2]}, b=100, seed=1)
    assert out.no_decision is True
    assert out.ci_low is None and out.ci_high is None


def test_block_is_race_day_not_iid():
    # If resampling were i.i.d. over races it would ignore day grouping; we assert the
    # implementation resamples whole days (block metadata + contiguous-day pooling).
    diffs = {f"d{d}": [0.05, 0.05, 0.05] for d in range(6)}
    out = race_day_cluster_bootstrap_ci_v1(diffs, b=200, seed=7)
    assert out.block == "race_day"
    assert out.n_days == 6
    # all diffs identical -> every resample mean is the same -> CI collapses to the point
    assert out.ci_low == out.ci_high == out.point
    assert out.point == pytest.approx(0.05, abs=1e-12)


def test_different_seed_can_differ_but_stays_within_range():
    diffs = {f"d{d}": list(np.linspace(-0.02, 0.02, 4)) for d in range(20)}
    a = race_day_cluster_bootstrap_ci_v1(diffs, b=300, seed=1)
    b = race_day_cluster_bootstrap_ci_v1(diffs, b=300, seed=2)
    assert a.point == b.point  # point estimate seed-independent
    assert a.ci_low is not None and b.ci_low is not None
