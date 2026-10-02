"""Feature 138 T004: centred one-sided p from the CI's own replicates + bounded counts cache."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from horseracing_eval import bootstrap as bs
from horseracing_eval.bootstrap import (
    block_bootstrap_counts,
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)


def _days(n: int) -> list[str]:
    d0 = dt.date(2026, 1, 3)
    return [(d0 + dt.timedelta(days=7 * i)).isoformat() for i in range(n)]


def _null_series(rng, n_days: int, roi: float):
    """Per-day stake (100 yen × bets) and payout with pooled ratio rescaled to exactly ``roi``."""
    stake = 100.0 * rng.integers(1, 6, size=n_days).astype(float)
    pay = rng.gamma(0.5, 2.0, size=n_days) * stake
    pay *= roi * stake.sum() / pay.sum()
    return stake, pay


def test_matches_explicit_null_centred_resampling():
    rng = np.random.default_rng(3)
    days = _days(60)
    stake, pay = _null_series(rng, 60, 1.25)
    res = race_block_ratio_bootstrap_ci_v1(pay, stake, days, b=2000, seed=11)
    point = float(res.point[0])
    p = centered_one_sided_p_from_replicates(res.replicates[0], point)
    counts = block_bootstrap_counts(60, 2000, 11).astype(float)
    null = (counts @ (pay / point)) / (counts @ stake)
    brute = (1 + int(np.sum(null >= point))) / 2001
    assert abs(p - brute) <= 2 / 2001  # same draws; only the division order differs


def test_exactly_break_even_is_about_half():
    rng = np.random.default_rng(5)
    stake, pay = _null_series(rng, 80, 1.0)
    res = race_block_ratio_bootstrap_ci_v1(pay, stake, _days(80), b=4000, seed=7)
    p = centered_one_sided_p_from_replicates(res.replicates[0], float(res.point[0]))
    assert 0.35 < p < 0.65


def test_roughly_uniform_under_the_null():
    hits = 0
    sims = 150
    for k in range(sims):
        rng = np.random.default_rng(1000 + k)
        stake = 100.0 * rng.integers(1, 6, size=50).astype(float)
        # true ROI 1: payout = stake × Gamma(mean 1) noise, NOT rescaled (sampling noise around 1)
        pay = stake * rng.gamma(0.5, 2.0, size=50)
        res = race_block_ratio_bootstrap_ci_v1(pay, stake, _days(50), b=999, seed=500 + k)
        if centered_one_sided_p_from_replicates(res.replicates[0], float(res.point[0])) <= 0.1:
            hits += 1
    assert 0.03 <= hits / sims <= 0.20


def test_strong_effect_gives_small_p_and_seed_reproduces():
    rng = np.random.default_rng(9)
    stake, pay = _null_series(rng, 120, 1.8)
    a = race_block_ratio_bootstrap_ci_v1(pay, stake, _days(120), b=3000, seed=21)
    b = race_block_ratio_bootstrap_ci_v1(pay, stake, _days(120), b=3000, seed=21)
    pa = centered_one_sided_p_from_replicates(a.replicates[0], float(a.point[0]))
    pb = centered_one_sided_p_from_replicates(b.replicates[0], float(b.point[0]))
    assert pa == pb and pa < 0.01


def test_nonpositive_point_and_nan_replicates():
    assert centered_one_sided_p_from_replicates([0.0, 0.5, 1.0], 0.0) == 1.0
    # NaN replicates (zero resampled denominator) never count as >=
    assert centered_one_sided_p_from_replicates([np.nan, np.nan, 4.0], 1.5) == pytest.approx(2 / 4)
    with pytest.raises(ValueError):
        centered_one_sided_p_from_replicates([], 1.0)


def test_counts_cache_is_bounded_lru():
    bs._COUNTS_CACHE.clear()
    for seed in range(12):
        block_bootstrap_counts(5, 10, seed)
    assert len(bs._COUNTS_CACHE) == bs._COUNTS_CACHE_MAXSIZE
    assert (5, 10, 0) not in bs._COUNTS_CACHE  # oldest evicted
    kept = block_bootstrap_counts(5, 10, 11)
    assert block_bootstrap_counts(5, 10, 11) is kept  # recent key served from the cache
    again = block_bootstrap_counts(5, 10, 0)  # evicted key recomputes the identical draw
    rng = np.random.default_rng(0)
    picks = rng.integers(0, 5, size=(10, 5))
    assert np.array_equal(again, np.stack([np.bincount(r, minlength=5) for r in picks]))
