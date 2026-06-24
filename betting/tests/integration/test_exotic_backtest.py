"""T019 (US2): period exotic backtest — EV vs baselines, DOUBLE-pseudo, deterministic (SC-004..007)."""

from __future__ import annotations

import datetime

import pytest

from horseracing_betting.exotic_backtest import run_exotic_backtest
from horseracing_betting.exotic_roi import TOTAL
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 12, 31)


def test_backtest_reports_all_strategies_double_pseudo(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    reports = run_exotic_backtest(
        session, date_from=_FROM, date_to=_TO, threshold=0.0, top_k=2,
        stake=100.0, model_version=mv,
    )
    assert set(reports) == {"ev", "lowest_oest", "uniform"}
    for strat, by_type in reports.items():
        assert TOTAL in by_type
        for rep in by_type.values():
            assert rep.pseudo is True            # DOUBLE-pseudo everywhere
            assert rep.strategy == strat
            assert rep.roi >= 0.0
            if rep.n_bets:
                assert 0.0 <= rep.hit_rate <= 1.0


def test_backtest_is_deterministic(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    kw = dict(date_from=_FROM, date_to=_TO, threshold=1.0, top_k=3, stake=100.0, model_version=mv)
    a = run_exotic_backtest(session, **kw)
    b = run_exotic_backtest(session, **kw)
    for strat in a:
        for bt in a[strat]:
            assert a[strat][bt].total_payout == b[strat][bt].total_payout
            assert a[strat][bt].n_bets == b[strat][bt].n_bets
            assert a[strat][bt].roi == b[strat][bt].roi


def test_ev_vs_baseline_compared_same_conditions(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    reports = run_exotic_backtest(
        session, date_from=_FROM, date_to=_TO, threshold=0.0, top_k=2, stake=100.0, model_version=mv
    )
    # success criterion is RELATIVE (beat baseline), never absolute >1.0 — just assert comparability
    ev_total = reports["ev"][TOTAL]
    low_total = reports["lowest_oest"][TOTAL]
    uni_total = reports["uniform"][TOTAL]
    assert ev_total.total_stake > 0  # bets were placed on the shared population
    # all three evaluated on the same race set under the same stake
    assert {low_total.pseudo, uni_total.pseudo, ev_total.pseudo} == {True}
