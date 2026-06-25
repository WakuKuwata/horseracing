"""T018 (012): real-odds-first / estimated fallback, real ROI scoring, void, dead-heat (SC-004/005)."""

from __future__ import annotations

from horseracing_db.enums import BetType

from horseracing_betting.exotic_ev import canonical_field
from horseracing_betting.exotic_recommend import _blended_bets
from horseracing_betting.exotic_roi import score_exotic
from horseracing_betting.exotic_types import ExoticBet, ExoticRaceOutcome

PREDS = {1: 0.55, 2: 0.20, 3: 0.12, 4: 0.08, 5: 0.03, 6: 0.02}
ODDS = {1: 4.0, 2: 3.0, 3: 4.5, 4: 6.0, 5: 12.0, 6: 20.0}


def _field():
    return canonical_field("R", PREDS, ODDS)


def test_blended_prefers_real_odds_row_level():
    field = _field()
    # give one exacta selection a real odds far above its estimate -> distinct row
    real = {(BetType.EXACTA, (1, 2)): 999.0}
    blended = _blended_bets(field, real, threshold=0.0, top_k=50, bet_types=(BetType.EXACTA,),
                            payout_rates=None, odds_cap=10000.0)
    by_sel = {tuple(b.selection): (odds, is_est, ev) for b, odds, is_est, ev in blended}
    # real-priced selection: is_estimated False, odds == real, EV uses real
    odds, is_est, ev = by_sel[(1, 2)]
    assert is_est is False and odds == 999.0
    # a different selection falls back to estimated (is_estimated True)
    other = next(v for k, v in by_sel.items() if k != (1, 2))
    assert other[1] is True


def _bet(bt, sel, o_est=5.0):
    return ExoticBet(bet_type=bt, selection=sel, p_model=0.2, o_est=o_est, ev=1.0)


FP = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}


def test_real_payout_vs_pseudo_payout_labeled():
    out = ExoticRaceOutcome("R", FP, field_size=8)
    bets = [_bet(BetType.EXACTA, [1, 2], o_est=9.0), _bet(BetType.EXACTA, [1, 3], o_est=9.0)]
    real = {(BetType.EXACTA, (1, 2)): 30.0}  # only [1,2] has a real dividend
    scored, skipped = score_exotic(bets, out, stake=100.0, real_odds=real)
    by_sel = {tuple(s.bet.selection): s for s in scored}
    assert by_sel[(1, 2)].pseudo is False and by_sel[(1, 2)].payout == 3000.0  # real ROI
    assert by_sel[(1, 3)].pseudo is True                                       # estimated fallback


def test_post_recommendation_scratch_is_voided():
    out = ExoticRaceOutcome("R", FP, field_size=8)
    bets = [_bet(BetType.QUINELLA, [1, 2]), _bet(BetType.QUINELLA, [1, 3])]
    scored, skipped = score_exotic(bets, out, stake=100.0, scratched={3})
    # the bet containing scratched horse 3 is voided (not scored)
    assert {tuple(s.bet.selection) for s in scored} == {(1, 2)}
    assert skipped == 1


def test_dead_heat_inherited_with_real_odds():
    dh = {1: 1, 2: 1, 3: 3, 4: 4, 5: 5}  # dead-heat for 1st
    out = ExoticRaceOutcome("R", dh, field_size=8)
    real = {(BetType.EXACTA, (1, 2)): 30.0, (BetType.WIDE, (1, 2)): 4.0}
    bets = [_bet(BetType.EXACTA, [1, 2]), _bet(BetType.WIDE, [1, 2])]
    scored, skipped = score_exotic(bets, out, stake=100.0, real_odds=real)
    # ordered exacta unscoreable under dead-heat -> skipped; wide inclusion still scores (real)
    assert skipped == 1
    assert len(scored) == 1 and scored[0].bet.bet_type == BetType.WIDE
    assert scored[0].pseudo is False and scored[0].payout == 400.0
