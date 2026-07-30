"""Combination portfolio scoring — settlement, staking, and the key-space trap.

The bug this file exists to prevent: the 009 engine keys UNORDERED bet types with a ``frozenset``,
whose iteration order is arbitrary. Turning one straight into a tuple produced keys like (10, 2)
that never matched the stored (2, 10), so most tickets silently scored 0 and wide/quinella/trio
reported ~0.32 against a 0.775 reference — a plausible-looking catastrophic loss rather than an
error. Ordered types were unaffected, which is what made the pattern diagnosable.
"""

from __future__ import annotations

import pytest

from horseracing_eval.exotic_portfolio import (
    K_GRID,
    MAX_SINGLE_HIT_SHARE,
    MIN_HITS,
    PRIMARY_BET_TYPES,
    PortfolioRace,
    holm_adjust,
    score_cell,
)


def _race(rid, day, probs, dividends, est=None, bet_type="quinella"):
    est = est or {c: 1.0 / max(p, 1e-9) for c, p in probs.items()}
    return PortfolioRace(rid, day, bet_type, probs, est, dividends)


def _pool(n_days=8, per_day=6, payout=0.775):
    """A structureless combination pool: the top combo wins 1/3 of the time and pays the
    fair-minus-takeout dividend, so a K=1 flat portfolio must return exactly ``payout``."""
    races, idx = [], 0
    combos = [(1, 2), (1, 3), (2, 3)]
    probs = {(1, 2): 0.5, (1, 3): 0.3, (2, 3): 0.2}
    for d in range(n_days):
        for _ in range(per_day):
            winner = combos[idx % 3]
            races.append(_race(f"{d}-{idx}", f"2026-02-{d + 1:02d}", probs,
                               {winner: payout / probs[winner]}))
            idx += 1
    return races


def test_only_the_winning_combination_pays():
    r = _race("r", "2026-02-01", {(1, 2): 0.5, (1, 3): 0.3}, {(1, 3): 4.0})
    res = score_cell([r], k=2, staking="flat", source="market", b=10, seed=1)
    assert res.n_bets == 2 and res.n_hits == 1
    assert res.roi == pytest.approx(4.0 / 2)


def test_a_structureless_pool_returns_one_minus_takeout():
    res = score_cell(_pool(), k=1, staking="flat", source="market", b=200, seed=3)
    # the top combo is bought every race and wins 1/3 of them, paying 0.775/0.5
    assert res.roi == pytest.approx(0.775 / 0.5 / 3, rel=0.35)


def test_inverse_odds_staking_equalises_expected_payout():
    """The 大阪事件 structure: total stake 1 per race, split so each ticket has the same expected
    payout at the ESTIMATED price (which is derived from win odds, not from the dividend)."""
    r = _race("r", "2026-02-01", {(1, 2): 0.6, (1, 3): 0.2, (2, 3): 0.2}, {(1, 3): 5.0})
    res = score_cell([r], k=3, staking="inverse_odds", source="market", b=10, seed=1)
    # stakes ∝ 1/O_est ∝ p, normalised: (0.6, 0.2, 0.2) -> payout 0.2 * 5.0 on the winner
    assert res.roi == pytest.approx(0.2 * 5.0 / 1.0)


def test_unordered_key_with_mixed_digit_widths_still_matches():
    """(2, 10) must match (2, 10) — the frozenset bug turned this into (10, 2) and scored 0."""
    r = _race("r", "2026-02-01", {(2, 10): 0.7, (1, 2): 0.3}, {(2, 10): 3.0})
    res = score_cell([r], k=1, staking="flat", source="market", b=10, seed=1)
    assert res.n_hits == 1 and res.roi == pytest.approx(3.0)


def test_too_few_hits_is_demoted_regardless_of_the_interval():
    r = [_race(f"r{i}", f"2026-02-{i % 8 + 1:02d}", {(1, 2): 0.9}, {(1, 2): 100.0})
         for i in range(MIN_HITS - 1)]
    res = score_cell(r, k=1, staking="flat", source="market", b=200, seed=1)
    assert res.verdict == "NO_DECISION" and "n_hits" in res.demoted_reason


def test_one_ticket_carrying_the_payout_is_demoted():
    races = [_race(f"r{i}", f"2026-02-{i % 8 + 1:02d}", {(1, 2): 0.9}, {(1, 2): 1.0})
             for i in range(60)]
    races.append(_race("jackpot", "2026-02-09", {(1, 2): 0.9}, {(1, 2): 100000.0}))
    res = score_cell(races, k=1, staking="flat", source="market", b=200, seed=1)
    assert res.max_single_hit_share > MAX_SINGLE_HIT_SHARE
    assert res.verdict == "NO_DECISION" and "carries" in res.demoted_reason
    assert res.leave_one_hit_out_roi < res.roi


def test_holm_screens_the_primary_family_and_ignores_exploratory_arms():
    """A single lucky cell among 24 must not survive; exploratory arms are never in the family."""
    fam = [score_cell(_pool(), k=k, staking="flat", source="market", b=100, seed=1)
           for k in K_GRID for _ in PRIMARY_BET_TYPES]
    survivors = holm_adjust(fam)
    assert all(v is False for v in survivors.values())
    assert all("|k=" in key for key in survivors)


def test_pre_registered_grid_is_frozen():
    assert K_GRID == (1, 3, 5, 10, 20, 50)
    assert PRIMARY_BET_TYPES == ("wide", "quinella", "exacta", "trio")
