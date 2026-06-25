"""T014 (013): estimate_market_odds(calibrator=...) — q' path, backward compat, recovery (SC-003)."""

from __future__ import annotations

import math

from horseracing_probability.fl_bias import fit_fl_calibrator
from horseracing_probability.market_odds import (
    estimate_market_odds,
)

BASE = {"A": 0.45, "B": 0.28, "C": 0.17, "D": 0.10}
ODDS = {"A": 2.2, "B": 3.6, "C": 6.0, "D": 11.0}


def _cal(gamma_bias=True):
    base_odds = {h: 1.0 / p for h, p in BASE.items()}
    samples = [(base_odds, "A" if i % 4 != 0 else "D") for i in range(150)]
    return fit_fl_calibrator(samples)


def test_calibrator_none_is_backward_compatible():
    raw = estimate_market_odds(ODDS)
    raw2 = estimate_market_odds(ODDS, calibrator=None)
    assert raw.win == raw2.win and raw.exacta == raw2.exacta  # identical, no behavior change


def test_calibrator_changes_estimated_odds():
    cal = _cal()
    raw = estimate_market_odds(ODDS)
    corr = estimate_market_odds(ODDS, calibrator=cal)
    # FL correction (γ≠1) shifts the win-odds estimates vs raw q
    assert raw.win != corr.win
    assert corr.is_estimated is True


def test_corrected_win_odds_do_not_recover_raw_odds():
    """Raw q recovers input win odds (≈ R·odds); corrected q' intentionally does NOT (bias removed)."""
    cal = _cal()
    raw = estimate_market_odds(ODDS)               # raw q recovers ≈ R·odds (010 identity)
    corr = estimate_market_odds(ODDS, calibrator=cal)
    # the favorite's corrected estimated win odds differ from the raw-recovered odds (bias removed)
    fav = "A"
    assert not math.isclose(raw.win[fav], corr.win[fav], rel_tol=1e-3)
