"""T012: exotic EV = P_model(009 on p) × O_est(010 on q), top-K, determinism (SC-001/SC-002)."""

from __future__ import annotations

from horseracing_db.enums import BetType
from horseracing_probability.engine import joint_probabilities
from horseracing_probability.market_odds import estimate_market_odds

from horseracing_betting.exotic_ev import canonical_field, exotic_ev_bets
from horseracing_betting.exotic_selection import selection_key

# Model is far more confident on horse 1 than the market (odds 4.0) -> EV value exists.
PREDS = {1: 0.55, 2: 0.20, 3: 0.12, 4: 0.08, 5: 0.03, 6: 0.02}
ODDS = {1: 4.0, 2: 3.0, 3: 4.5, 4: 6.0, 5: 12.0, 6: 20.0}


def _field():
    return canonical_field("R", PREDS, ODDS)


def test_ev_equals_pmodel_times_oest():
    field = _field()
    joint = joint_probabilities(field.p_norm, field_size=field.field_size)
    est = estimate_market_odds(field.odds_norm, field_size=field.field_size)
    bets = exotic_ev_bets(field, threshold=0.0, top_k=50, bet_types=(BetType.EXACTA,))
    for b in bets:
        key = (b.selection[0], b.selection[1])
        assert abs(b.p_model - joint.exacta[key]) < 1e-12
        assert abs(b.o_est - est.exacta[key]) < 1e-9
        assert abs(b.ev - b.p_model * b.o_est) < 1e-12


def test_threshold_filters_below():
    field = _field()
    bets = exotic_ev_bets(field, threshold=1.0, top_k=50, bet_types=(BetType.EXACTA,))
    assert bets  # some value bets exist
    assert all(b.ev >= 1.0 - 1e-9 for b in bets)


def test_top_k_limit_per_bet_type():
    field = _field()
    bets = exotic_ev_bets(field, threshold=0.0, top_k=3, bet_types=(BetType.TRIFECTA,))
    assert len(bets) == 3
    # sorted by (-ev, selection_key)
    evs = [b.ev for b in bets]
    assert evs == sorted(evs, reverse=True)


def test_deterministic_order_tiebreak():
    field = _field()
    a = exotic_ev_bets(field, threshold=0.0, top_k=10, bet_types=(BetType.TRIO,))
    b = exotic_ev_bets(field, threshold=0.0, top_k=10, bet_types=(BetType.TRIO,))
    assert [(x.bet_type, x.selection) for x in a] == [(y.bet_type, y.selection) for y in b]
    keys = [selection_key(x.bet_type, x.selection) for x in a]
    # equal-EV ties resolved by selection_key ascending
    for i in range(len(a) - 1):
        if abs(a[i].ev - a[i + 1].ev) < 1e-12:
            assert keys[i] <= keys[i + 1]


def test_dict_top_k_per_bet_type():
    field = _field()
    bets = exotic_ev_bets(
        field, threshold=0.0, top_k={BetType.EXACTA: 2, BetType.TRIO: 4},
        bet_types=(BetType.EXACTA, BetType.TRIO),
    )
    n_exacta = sum(1 for b in bets if b.bet_type == BetType.EXACTA)
    n_trio = sum(1 for b in bets if b.bet_type == BetType.TRIO)
    assert n_exacta == 2 and n_trio == 4


def test_p_and_q_not_mixed():
    """O_est must come from market odds q, NOT from model p — different inputs, different result."""
    field = _field()
    bets_real = exotic_ev_bets(field, threshold=0.0, top_k=50, bet_types=(BetType.QUINELLA,))
    # if O_est were (wrongly) derived from p, swapping odds would not change O_est. It must.
    skewed = canonical_field("R", PREDS, {1: 30.0, 2: 20.0, 3: 9.0, 4: 5.0, 5: 3.6, 6: 2.2})
    bets_skew = exotic_ev_bets(skewed, threshold=0.0, top_k=50, bet_types=(BetType.QUINELLA,))
    real = {tuple(b.selection): b.o_est for b in bets_real}
    skew = {tuple(b.selection): b.o_est for b in bets_skew}
    assert real != skew  # O_est responds to market odds, proving q≠p


def test_small_field_skips_impossible_bet_types():
    # 2 horses: exacta/quinella possible, trio/trifecta/place(≤4)/wide(N<3) not
    field = canonical_field("R", {1: 0.6, 2: 0.4}, {1: 1.8, 2: 2.5})
    bets = exotic_ev_bets(field, threshold=0.0, top_k=10)
    types = {b.bet_type for b in bets}
    assert BetType.TRIFECTA not in types and BetType.TRIO not in types
    assert BetType.PLACE not in types and BetType.WIDE not in types
