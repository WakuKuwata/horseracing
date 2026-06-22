"""US2: baselines satisfy the Predictor contract + consistency (incl. small fields)."""

from __future__ import annotations

import datetime

from horseracing_eval.baselines import MarketBaseline, UniformBaseline
from horseracing_eval.consistency import check_consistency
from horseracing_eval.predictor import HorseEntry, RaceContext, ResultMarket


def _ctx(odds_list):
    horses = tuple(
        HorseEntry(horse_id=f"H{i}", horse_number=i + 1,
                   result_market=ResultMarket(odds=o, popularity=None))
        for i, o in enumerate(odds_list)
    )
    return RaceContext("200801010101", datetime.date(2008, 1, 1), horses)


def test_market_consistency_and_monotonic():
    preds = MarketBaseline().predict_race(_ctx([2.0, 4.0, 6.0, 8.0, 10.0, 12.0]))
    check_consistency(preds)
    for p in preds.values():
        assert 0 <= p.win <= p.top2 <= p.top3 <= 1 + 1e-9


def test_market_favorite_has_highest_win():
    preds = MarketBaseline().predict_race(_ctx([2.0, 4.0, 6.0]))
    assert preds["H0"].win > preds["H1"].win > preds["H2"].win


def test_market_handles_missing_or_zero_odds():
    preds = MarketBaseline().predict_race(_ctx([2.0, None, 0.0, 8.0]))
    check_consistency(preds)  # no zero-prob crash, sums valid


def test_uniform_consistency():
    preds = UniformBaseline().predict_race(_ctx([0, 0, 0, 0, 0, 0]))
    check_consistency(preds)
    assert all(abs(p.win - 1 / 6) < 1e-12 for p in preds.values())


def test_uniform_small_field_n2():
    preds = UniformBaseline().predict_race(_ctx([0, 0]))  # N=2 < 3
    check_consistency(preds)  # target top3 = min(3, 2) = 2, passes


def test_leaky_reference_markers():
    assert MarketBaseline.is_leaky_reference is True   # FR-013 marker
    assert UniformBaseline.is_leaky_reference is False
