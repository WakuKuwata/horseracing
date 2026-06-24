"""T018: ROI baselines on the shared canonical field, deterministic (SC-005/FR-009)."""

from __future__ import annotations

from horseracing_db.enums import BetType

from horseracing_betting.exotic_ev import candidate_bets, canonical_field
from horseracing_betting.exotic_selection import selection_key
from horseracing_betting.exotic_strategies import lowest_oest_baseline, uniform_baseline

PREDS = {1: 0.40, 2: 0.25, 3: 0.18, 4: 0.10, 5: 0.04, 6: 0.03}
ODDS = {1: 2.2, 2: 3.6, 3: 5.0, 4: 9.0, 5: 20.0, 6: 30.0}


def _field():
    return canonical_field("R", PREDS, ODDS)


def test_lowest_oest_picks_minimum_estimated_odds():
    field = _field()
    bets = lowest_oest_baseline(field, top_k=3, bet_types=(BetType.EXACTA,))
    all_cands = candidate_bets(field, bet_types=(BetType.EXACTA,))[BetType.EXACTA]
    cheapest3 = sorted(all_cands, key=lambda b: (b.o_est, selection_key(b.bet_type, b.selection)))[:3]
    assert [b.selection for b in bets] == [b.selection for b in cheapest3]
    # ascending O_est
    assert bets[0].o_est <= bets[1].o_est <= bets[2].o_est


def test_lowest_oest_tiebreak_deterministic():
    field = _field()
    a = lowest_oest_baseline(field, top_k=5, bet_types=(BetType.TRIO,))
    b = lowest_oest_baseline(field, top_k=5, bet_types=(BetType.TRIO,))
    assert [x.selection for x in a] == [y.selection for y in b]


def test_uniform_is_deterministic_for_seed():
    field = _field()
    a = uniform_baseline(field, top_k=4, bet_types=(BetType.TRIO,), seed=11011)
    b = uniform_baseline(field, top_k=4, bet_types=(BetType.TRIO,), seed=11011)
    assert [x.selection for x in a] == [y.selection for y in b]
    assert len(a) == 4


def test_uniform_different_seed_can_differ():
    field = _field()
    a = uniform_baseline(field, top_k=3, bet_types=(BetType.TRIFECTA,), seed=1)
    b = uniform_baseline(field, top_k=3, bet_types=(BetType.TRIFECTA,), seed=37)
    # not asserting inequality always, but selections must be valid candidates
    cand_keys = {
        tuple(c.selection)
        for c in candidate_bets(field, bet_types=(BetType.TRIFECTA,))[BetType.TRIFECTA]
    }
    assert all(tuple(x.selection) in cand_keys for x in a + b)


def test_baselines_use_same_population_as_ev():
    field = _field()
    cand = candidate_bets(field, bet_types=(BetType.QUINELLA,))[BetType.QUINELLA]
    pop_keys = {tuple(c.selection) for c in cand}
    low = lowest_oest_baseline(field, top_k=100, bet_types=(BetType.QUINELLA,))
    uni = uniform_baseline(field, top_k=100, bet_types=(BetType.QUINELLA,))
    assert {tuple(b.selection) for b in low} <= pop_keys
    assert {tuple(b.selection) for b in uni} <= pop_keys
