"""US1 (SC-001): market-implied q and win-odds recovery (codex-verified: odds=R/s -> q=s)."""

from __future__ import annotations

import pytest

from horseracing_probability.market_calibration import recover_win_odds
from horseracing_probability.market_odds import estimate_market_odds, market_implied_win_probs

_ATOL = 1e-9
# s=[0.5,0.25,0.25], R_win=0.8 -> odds=R/s=[1.6,3.2,3.2], Σ1/odds=1.25, R·S=1 -> exact recovery
_ODDS = {"A": 1.6, "B": 3.2, "C": 3.2}


def test_market_implied_is_vote_share():
    q = market_implied_win_probs(_ODDS)
    assert q["A"] == pytest.approx(0.5, abs=_ATOL)
    assert q["B"] == pytest.approx(0.25, abs=_ATOL)
    assert sum(q.values()) == pytest.approx(1.0, abs=_ATOL)


def test_win_odds_recovered_exactly_when_RS_equals_1():
    eo = estimate_market_odds(_ODDS)  # default win payout 0.80, R·S=1
    assert eo.win["A"] == pytest.approx(1.6, abs=_ATOL)
    assert eo.win["B"] == pytest.approx(3.2, abs=_ATOL)
    hat = recover_win_odds(_ODDS, payout_rate_win=0.80)
    assert hat["A"] == pytest.approx(1.6, abs=_ATOL)


def test_uniform_ratio_when_RS_not_1():
    # payout 0.75 instead of 0.80: hat/odds = R·S = 0.75·1.25 = 0.9375 for every horse
    eo = estimate_market_odds(_ODDS, payout_rates={"win": 0.75})
    for h, o in _ODDS.items():
        assert eo.win[h] / o == pytest.approx(0.9375, abs=_ATOL)
