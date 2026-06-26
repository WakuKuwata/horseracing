"""T018: deterministic Kelly core — allocation and seeded bootstrap reproduce exactly (016, SC-003).

The allocation optimizer uses no RNG; the ruin bootstrap is seeded. Same inputs (and same seed) →
identical outputs, so persisted Kelly recommendations and backtest ruin estimates are reproducible
(analyze F1: the seed is the only randomness, and it is recorded).
"""

from __future__ import annotations

from horseracing_betting.kelly_allocation import allocate_kelly, maximize_log_growth
from horseracing_betting.kelly_backtest import _bootstrap_ruin
from horseracing_betting.kelly_types import KellyConfig


def test_allocation_is_deterministic():
    group = [(0.4, 4.0), (0.3, 5.0), (0.2, 8.0)]
    raw = [(p, o, False, (p * o - 1.0) / (o - 1.0)) for p, o in group]
    cfg = KellyConfig(lambda_real=0.5, cap_bet=0.05, cap_total=0.10, o_min=1.0)
    a = allocate_kelly(raw, cfg=cfg)
    b = allocate_kelly(raw, cfg=cfg)
    assert a == b
    assert maximize_log_growth([0.4, 0.3], [4.0, 5.0]) == maximize_log_growth([0.4, 0.3], [4.0, 5.0])


def test_bootstrap_ruin_reproducible_with_seed():
    returns = [1.2, 0.8, 0.9, 1.5, 0.7, 1.1, 0.6, 1.3]
    a = _bootstrap_ruin(returns, bankroll0=100.0, ruin_threshold=0.0, n_samples=200, seed=42)
    b = _bootstrap_ruin(returns, bankroll0=100.0, ruin_threshold=0.0, n_samples=200, seed=42)
    assert a == b                       # same seed → identical estimate
    assert 0.0 <= a <= 1.0


def test_bootstrap_ruin_varies_with_threshold():
    returns = [1.2, 0.5, 0.9, 1.5, 0.4, 1.1]
    low = _bootstrap_ruin(returns, bankroll0=100.0, ruin_threshold=0.0, n_samples=200, seed=7)
    high = _bootstrap_ruin(returns, bankroll0=100.0, ruin_threshold=90.0, n_samples=200, seed=7)
    assert high >= low                  # easier-to-hit threshold → not fewer ruins
