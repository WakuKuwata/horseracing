"""The market-side stage discount on estimated exotic odds.

Plain Harville (λ=1) overstates how often a favourite fills the minor placings, and that error
compounds into every derived combination price. Measured against 51 races of REAL exotic grids
(28k combinations) the λ=1 estimate sat at 0.76 / 0.70 / 0.82 of the market's own 馬連 / ワイド /
三連複 price; the 084 λ moved those medians to 0.93 / 0.93 / 1.13 on races held out of the fit.

Two properties matter and are easy to get wrong:
* the legacy path must stay byte-identical (a silent renumbering of every stored recommendation
  would be far worse than the bias being fixed), and
* the p-side (049: 0.852/0.707) and q-side (084: 0.8312/0.7101) fits are DIFFERENT quantities.
"""

from __future__ import annotations

import pathlib

import pytest
from horseracing_eval.stage_discount import StageDiscount

from horseracing_probability.market_odds import (
    MARKET_STAGE_LAMBDA2,
    MARKET_STAGE_LAMBDA3,
    default_market_stage_discount,
    estimate_market_odds,
)

ODDS = {"1": 2.0, "2": 3.5, "3": 6.0, "4": 12.0, "5": 25.0, "6": 40.0, "7": 80.0, "8": 150.0}


def test_omitting_the_discount_is_byte_identical_to_the_legacy_path():
    a = estimate_market_odds(ODDS, field_size=len(ODDS))
    b = estimate_market_odds(ODDS, field_size=len(ODDS), stage_discount=None)
    for bet in ("quinella", "wide", "trio", "exacta", "trifecta", "place"):
        assert getattr(a, bet) == getattr(b, bet), bet


def test_identity_lambda_reproduces_the_legacy_numbers():
    legacy = estimate_market_odds(ODDS, field_size=len(ODDS))
    ident = estimate_market_odds(ODDS, field_size=len(ODDS),
                                 stage_discount=StageDiscount(lambda2=1.0, lambda3=1.0))
    assert ident.quinella == legacy.quinella


def test_win_odds_are_untouched_by_the_discount():
    """λ1 is fixed at 1, so the win marginal — the one thing the market gives us directly — must
    not move. If it did, the discount would be rewriting its own input."""
    legacy = estimate_market_odds(ODDS, field_size=len(ODDS))
    disc = estimate_market_odds(ODDS, field_size=len(ODDS),
                                stage_discount=default_market_stage_discount())
    assert disc.win == legacy.win


def test_the_discount_lowers_derived_combination_prices():
    """The bias runs one way: λ<1 moves probability mass off the favourites into the rest of the
    grid, so the typical combination gets a SHORTER estimated price — which is the direction the
    real grids said we were wrong in."""
    legacy = estimate_market_odds(ODDS, field_size=len(ODDS))
    disc = estimate_market_odds(ODDS, field_size=len(ODDS),
                                stage_discount=default_market_stage_discount())
    for bet in ("quinella", "wide", "trio"):
        lo = getattr(legacy, bet)
        hi = getattr(disc, bet)
        cheaper = sum(1 for k in lo if hi[k] < lo[k])
        assert cheaper > len(lo) * 0.7, f"{bet}: only {cheaper}/{len(lo)} combinations came down"


def test_the_market_fit_is_its_own_quantity():
    """Three different λ pairs live in this repo and none may stand in for another:

    * 049  0.852 / 0.707  — fitted on MODEL p, for the displayed top2/top3 probabilities
    * 084  0.8312 / 0.7101 — fitted on market q against top-3 popularity composition (chaos artifact)
    * here 0.75 / 0.70    — fitted against REAL exotic price grids (1,001 races)

    Pinning the value keeps a future edit from silently adopting one of the others.
    """
    assert (MARKET_STAGE_LAMBDA2, MARKET_STAGE_LAMBDA3) == (0.75, 0.70)
    assert (MARKET_STAGE_LAMBDA2, MARKET_STAGE_LAMBDA3) != (0.852, 0.707), "that is the 049 p-side"
    assert (MARKET_STAGE_LAMBDA2, MARKET_STAGE_LAMBDA3) != (0.8312, 0.7101), "that is 084's"


def test_the_chaos_artifact_lambda_is_not_taken_from_here():
    """084's readout must keep reading its own artifact. If it ever imported these constants, a
    change made for exotic PRICING would silently move the 荒れ度 calibration too."""
    src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "horseracing_probability"
           / "chaos_artifact.py").read_text(encoding="utf-8")
    assert "MARKET_STAGE_LAMBDA" not in src


def test_default_helper_matches_the_constants():
    sd = default_market_stage_discount()
    assert (sd.lambda2, sd.lambda3) == (MARKET_STAGE_LAMBDA2, MARKET_STAGE_LAMBDA3)
    assert not sd.is_identity


def test_probabilities_stay_normalised_under_the_discount():
    """049's invariant: discounting reweights the placing stages, it must not leak mass."""
    disc = estimate_market_odds(ODDS, field_size=len(ODDS),
                                stage_discount=default_market_stage_discount())
    assert all(o > 0 for o in disc.quinella.values())
    assert all(o > 0 for o in disc.trio.values())


@pytest.mark.parametrize("n", [5, 8, 12, 18])
def test_holds_across_field_sizes(n):
    odds = {str(i): 2.0 + 3.0 * i for i in range(1, n + 1)}
    disc = estimate_market_odds(odds, field_size=n,
                                stage_discount=default_market_stage_discount())
    assert disc.quinella and all(o > 0 for o in disc.quinella.values())
