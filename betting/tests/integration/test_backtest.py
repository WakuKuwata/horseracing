"""US2/US3 (SC-003/004/006): pseudo-ROI backtest returns same-set reports vs ROI baselines."""

from __future__ import annotations

import datetime

import pytest

from horseracing_betting.backtest import run_backtest
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration


def test_backtest_compares_strategies_same_race_set(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=12, field_size=8)
    mv = make_active_model(session, tmp_path)

    reports = run_backtest(
        session, start_date=datetime.date(2008, 1, 1), end_date=datetime.date(2008, 12, 31),
        model_version=mv, threshold=1.0, stake=100.0,
    )
    assert set(reports) == {"ev", "favorite", "uniform"}
    ev, fav, uni = reports["ev"], reports["favorite"], reports["uniform"]

    # same evaluated race set for all strategies
    assert ev.n_races == fav.n_races == uni.n_races
    assert ev.n_races > 0
    # all pseudo; in-sample because the model was trained through 2008
    assert all(r.pseudo for r in reports.values())
    assert all(r.in_sample for r in reports.values())
    # favorite bets exactly one horse per bet race; uniform bets at least as many as ev
    assert fav.n_bets == fav.n_bet_races
    assert uni.n_bets >= ev.n_bets
    # recovery rates are finite, non-negative
    assert all(r.recovery_rate >= 0 for r in reports.values())
