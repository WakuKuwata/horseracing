"""US1 (SC-002/005, leak): estimate_market_odds runs the 009 engine on q; uses NO model p."""

from __future__ import annotations

import pytest

from horseracing_probability.engine import joint_probabilities
from horseracing_probability.market_odds import estimate_market_odds, market_implied_win_probs

_ATOL = 1e-9
_ODDS = {"A": 2.0, "B": 4.0, "C": 8.0}


def test_estimated_odds_are_payout_over_market_prob():
    q = market_implied_win_probs(_ODDS)
    jp = joint_probabilities(q)
    eo = estimate_market_odds(_ODDS)
    # estimated odds = R_b / P_market(c)
    assert eo.exacta[("A", "B")] == pytest.approx(0.75 / jp.exacta[("A", "B")], abs=_ATOL)
    assert eo.trifecta[("A", "B", "C")] == pytest.approx(0.725 / jp.trifecta[("A", "B", "C")], abs=_ATOL)
    assert eo.is_estimated is True
    assert eo.payout_rates["trifecta"] == 0.725


def test_underlying_exacta_probs_sum_to_one():
    # Σ_c (R_exacta / est_odds(c)) = Σ_c P_market(c) = 1 (consistency carried through odds)
    eo = estimate_market_odds(_ODDS)
    assert sum(0.75 / v for v in eo.exacta.values()) == pytest.approx(1.0, abs=1e-9)


def test_uses_market_odds_only_and_is_deterministic():
    # signature takes win_odds only — no model p can enter; output is reproducible
    assert estimate_market_odds(_ODDS) == estimate_market_odds(_ODDS)
