"""Feature 109 T008: vectorised block ratio bootstrap vs the per-series race-day loop."""

from __future__ import annotations

import numpy as np

from horseracing_eval.bootstrap import (
    race_block_ratio_bootstrap_ci_v1,
    race_day_cluster_ratio_bootstrap_ci_v1,
)


def _series(rng, n_days=40):
    days = [f"2010-01-{i + 1:02d}" if i < 31 else f"2010-02-{i - 30:02d}" for i in range(n_days)]
    num = rng.gamma(2.0, 10.0, size=n_days)
    den = rng.integers(5, 30, size=n_days).astype(float)
    return days, num, den


def test_race_day_matches_loop_version_within_1e12():
    rng = np.random.default_rng(1)
    days, num, den = _series(rng)
    ref = race_day_cluster_ratio_bootstrap_ci_v1({d: [float(x)] for d, x in zip(days, num, strict=True)},
                                                 {d: [float(x)] for d, x in zip(days, den, strict=True)},
                                                 b=300, seed=77)
    new = race_block_ratio_bootstrap_ci_v1(num, den, days, block="race_day", b=300, seed=77)
    assert new.n_blocks == len(days)
    assert abs(new.point[0] - ref.point) <= 1e-12
    np.testing.assert_allclose(new.replicates[0], np.asarray(ref.replicates), rtol=1e-12, atol=0)
    assert abs(new.ci_low[0] - ref.ci_low) <= 1e-9
    assert abs(new.ci_high[0] - ref.ci_high) <= 1e-9


def test_two_rows_share_the_same_block_draws():
    rng = np.random.default_rng(2)
    days, num, den = _series(rng)
    num2 = np.vstack([num, num * 0.5 + 1.0])
    den2 = np.vstack([den, den])
    both = race_block_ratio_bootstrap_ci_v1(num2, den2, days, b=150, seed=5)
    solo = race_block_ratio_bootstrap_ci_v1(num2[1], den2[1], days, b=150, seed=5)
    np.testing.assert_array_equal(both.replicates[1], solo.replicates[0])


def test_week_and_month_blocks_reduce_block_count_and_move_days_together():
    rng = np.random.default_rng(3)
    days, num, den = _series(rng)
    day = race_block_ratio_bootstrap_ci_v1(num, den, days, block="race_day", b=100, seed=9)
    week = race_block_ratio_bootstrap_ci_v1(num, den, days, block="iso_week", b=100, seed=9)
    month = race_block_ratio_bootstrap_ci_v1(num, den, days, block="calendar_month", b=100, seed=9)
    assert month.n_blocks < week.n_blocks < day.n_blocks
    # two days in the same ISO week and nothing else → one block → undecidable
    d2 = ["2010-01-05", "2010-01-06"]
    one = race_block_ratio_bootstrap_ci_v1([1.0, 2.0], [1.0, 1.0], d2, block="iso_week", b=10, seed=1)
    assert one.no_decision and one.n_blocks == 1
    # three days, two share a week: replicates only ever combine the two together
    d3 = ["2010-01-05", "2010-01-06", "2010-01-20"]
    r = race_block_ratio_bootstrap_ci_v1([1.0, 3.0, 10.0], [1.0, 1.0, 1.0], d3, block="iso_week", b=200, seed=2)
    allowed = {(1 + 3) / 2, 10.0, (1 + 3 + 10) / 3, (1 + 3 + 1 + 3) / 4, (10 + 10) / 2}
    vals = set(np.round(r.replicates[0], 9).tolist())
    assert vals <= {round(v, 9) for v in allowed}


def test_same_input_twice_is_bit_identical():
    rng = np.random.default_rng(4)
    days, num, den = _series(rng)
    a = race_block_ratio_bootstrap_ci_v1(num, den, days, b=100, seed=11)
    b = race_block_ratio_bootstrap_ci_v1(num, den, days, b=100, seed=11)
    np.testing.assert_array_equal(a.replicates, b.replicates)
    assert a.point.tobytes() == b.point.tobytes()


def test_zero_denominator_replicates_are_counted_not_dropped():
    days = ["2010-01-01", "2010-01-02", "2010-01-03"]
    r = race_block_ratio_bootstrap_ci_v1([0.0, 0.0, 5.0], [0.0, 0.0, 1.0], days, b=500, seed=3)
    assert r.n_zero_den[0] > 0
    assert np.isnan(r.replicates[0]).sum() == r.n_zero_den[0]
