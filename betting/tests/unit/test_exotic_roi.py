"""T017: per-bet-type scoring, field-size branches, place/wide multi-hit bet-level (SC-004)."""

from __future__ import annotations

from horseracing_db.enums import BetType

from horseracing_betting.exotic_roi import TOTAL, aggregate_roi, score_exotic
from horseracing_betting.exotic_types import ExoticBet, ExoticRaceOutcome


def _bet(bt, sel, o_est=5.0):
    return ExoticBet(bet_type=bt, selection=sel, p_model=0.2, o_est=o_est, ev=1.0)


# finishing positions for an 8-horse race
FP8 = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}


def test_exacta_ordered_payout_and_miss():
    out = ExoticRaceOutcome("R", FP8, field_size=8)
    scored, _ = score_exotic(
        [_bet(BetType.EXACTA, [1, 2], 9.0), _bet(BetType.EXACTA, [2, 1], 9.0)], out, stake=100.0
    )
    assert scored[0].hit is True and scored[0].payout == 900.0
    assert scored[1].hit is False and scored[1].payout == 0.0


def test_place_wide_multiple_hits_scored_bet_level():
    out = ExoticRaceOutcome("R", FP8, field_size=8)
    bets = [
        _bet(BetType.PLACE, [1], 2.0), _bet(BetType.PLACE, [2], 3.0),  # both in top3 -> both hit
        _bet(BetType.WIDE, [1, 2], 4.0), _bet(BetType.WIDE, [1, 3], 5.0),  # both pairs in top3
    ]
    scored, _ = score_exotic(bets, out, stake=100.0)
    assert all(s.hit for s in scored)  # 4 independent hits, not race-capped
    assert sum(s.payout for s in scored) == 100 * (2.0 + 3.0 + 4.0 + 5.0)


def test_field_size_branches_for_wide():
    bets = [_bet(BetType.WIDE, [1, 3], 5.0)]
    assert score_exotic(bets, ExoticRaceOutcome("R", FP8, 8), stake=100)[0][0].hit is True  # top3
    fp7 = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7}
    assert score_exotic(bets, ExoticRaceOutcome("R", fp7, 7), stake=100)[0][0].hit is False  # top2


def test_place_none_for_tiny_field_is_unscoreable():
    fp4 = {1: 1, 2: 2, 3: 3, 4: 4}
    scored, unscoreable = score_exotic(
        [_bet(BetType.PLACE, [1])], ExoticRaceOutcome("R", fp4, 4), stake=100.0
    )
    assert scored == [] and unscoreable == 1  # ≤4 -> no place bet


def test_aggregate_roi_and_skip_rate():
    out = ExoticRaceOutcome("R", FP8, field_size=8)
    bets = [_bet(BetType.PLACE, [1], 2.0), _bet(BetType.PLACE, [5], 2.0)]  # one hit, one miss
    scored, _ = score_exotic(bets, out, stake=100.0)
    reports = aggregate_roi(
        scored, strategy="ev",
        opportunities={BetType.PLACE: 4}, skipped={BetType.PLACE: 1},
    )
    r = reports[BetType.PLACE]
    assert r.n_bets == 2 and r.n_hits == 1
    assert r.hit_rate == 0.5
    assert r.total_stake == 200.0 and r.total_payout == 200.0
    assert r.roi == 1.0
    assert r.skip_rate == 0.25  # 1 / 4
    assert r.pseudo is True
    assert TOTAL in reports


def test_max_drawdown_and_losing_streak():
    out = ExoticRaceOutcome("R", FP8, field_size=8)
    # three losing exacta bets in a row (wrong order) -> dd grows, streak 3
    bets = [_bet(BetType.EXACTA, [2, 1]) for _ in range(3)]
    scored, _ = score_exotic(bets, out, stake=100.0)
    reports = aggregate_roi(scored, strategy="ev")
    r = reports[BetType.EXACTA]
    assert r.max_consecutive_losses == 3
    assert r.max_drawdown == 300.0
