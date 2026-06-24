"""US1 (SC-003/006): invalid-odds exclusion, place field-size, cap/None, determinism."""

from __future__ import annotations

import pytest

from horseracing_probability.market_odds import (
    DEFAULT_PAYOUT_RATES,
    MarketOddsError,
    estimate_market_odds,
)


def test_invalid_odds_excluded_from_population():
    eo = estimate_market_odds({"A": 2.0, "B": 4.0, "C": None, "D": 0.0, "E": -1.0})
    assert set(eo.win) == {"A", "B"}  # null / 0 / negative odds dropped


def test_no_valid_odds_raises():
    with pytest.raises(MarketOddsError):
        estimate_market_odds({"A": None, "B": 0.0})


def test_place_field_size_rules():
    odds = {chr(65 + i): 2.0 + i for i in range(10)}
    assert estimate_market_odds(odds, field_size=3).place is None      # <=4 -> none
    assert estimate_market_odds(odds, field_size=6).place is not None  # 5-7 -> top2 inclusion
    assert estimate_market_odds(odds, field_size=10).place is not None # 8+ -> top3 inclusion


def test_cap_limits_derived_odds():
    odds = {"A": 1.2, "B": 60.0, "C": 60.0, "D": 60.0}
    capped = estimate_market_odds(odds, odds_cap=100.0)
    uncapped = estimate_market_odds(odds, odds_cap=1e9)
    assert all(v is None or v <= 100.0 + 1e-9 for v in capped.trifecta.values())
    # the cap actually bit (some uncapped trifecta odds exceed 100) and only the DERIVED odds change
    assert any(v is not None and v > 100.0 for v in uncapped.trifecta.values())


def test_deterministic():
    o = {"A": 2.0, "B": 4.0, "C": 8.0}
    assert estimate_market_odds(o) == estimate_market_odds(o)


def test_default_payout_rates_official():
    assert DEFAULT_PAYOUT_RATES["win"] == 0.80 and DEFAULT_PAYOUT_RATES["trifecta"] == 0.725
