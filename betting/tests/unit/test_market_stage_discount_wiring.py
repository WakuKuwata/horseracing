"""The market-side stage discount is ON for bet selection, and the audit string says so.

`estimate_market_odds` prices every exotic combination from the win pool. Plain Harville (λ=1)
prices them far too generously — measured at 0.76 / 0.70 / 0.82 of the real 馬連 / ワイド / 三連複
grid over 51 races — which inflates EV on exactly the combinations a bet search would chase. The
084 market-q λ is therefore the default here.

Because that changes the price behind every stored recommendation, `logic_version` has to record
it: without the marker, rows written before and after the change are indistinguishable and the
stored EV is no longer reproducible (constitution V).
"""

from __future__ import annotations

from horseracing_probability.market_odds import MARKET_STAGE_LAMBDA2, MARKET_STAGE_LAMBDA3

from horseracing_betting.exotic_ev import candidate_bets
from horseracing_betting.exotic_recommend import default_exotic_logic_version
from horseracing_betting.exotic_types import CanonicalField

_P = {1: 0.35, 2: 0.25, 3: 0.15, 4: 0.10, 5: 0.06, 6: 0.05, 7: 0.03, 8: 0.01}
_O = {1: 2.5, 2: 3.5, 3: 6.0, 4: 9.0, 5: 15.0, 6: 18.0, 7: 30.0, 8: 90.0}


def _field():
    return CanonicalField(
        race_id="202601010101", horse_numbers=sorted(_P), p_norm=dict(_P), odds_norm=dict(_O),
        field_size=len(_P), number_to_id={n: f"h{n}" for n in _P},
    )


def test_logic_version_records_the_market_discount():
    lv = default_exotic_logic_version(
        threshold=1.0, top_k=3, stake=100.0,
        payout_rates={"quinella": 0.775}, odds_cap=10000.0,
    )
    assert f"mktsd=l2:{MARKET_STAGE_LAMBDA2},l3:{MARKET_STAGE_LAMBDA3}" in lv


def test_selection_uses_the_discount_by_default():
    """Default vs explicitly-disabled must differ — if they matched, the default is not wired."""
    on = candidate_bets(_field(), bet_types=("quinella",))
    off = candidate_bets(_field(), bet_types=("quinella",), market_stage_discount=None)
    on_odds = {tuple(b.selection): b.o_est for b in on["quinella"]}
    off_odds = {tuple(b.selection): b.o_est for b in off["quinella"]}
    assert on_odds != off_odds
    cheaper = sum(1 for k in on_odds if on_odds[k] < off_odds[k])
    assert cheaper > len(on_odds) * 0.7, "the discount should shorten most estimated prices"


def test_disabling_reproduces_the_legacy_prices():
    """The escape hatch must be exact, so a historical run can still be recomputed."""
    a = candidate_bets(_field(), bet_types=("quinella",), market_stage_discount=None)
    b = candidate_bets(_field(), bet_types=("quinella",), market_stage_discount=None)
    assert [(tuple(x.selection), x.o_est) for x in a["quinella"]] == \
           [(tuple(x.selection), x.o_est) for x in b["quinella"]]


def test_model_probability_is_not_touched_by_the_market_discount():
    """p and q stay separate (p≠q): discounting the MARKET price must not move P_model."""
    on = {tuple(b.selection): b.p_model for b in candidate_bets(_field(), bet_types=("trio",))["trio"]}
    off = {tuple(b.selection): b.p_model
           for b in candidate_bets(_field(), bet_types=("trio",), market_stage_discount=None)["trio"]}
    assert on == off
