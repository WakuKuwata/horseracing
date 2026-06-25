"""T019 (US2): real exotic odds drive real-ROI recommendations; missing -> estimated fallback."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import ExoticOdds, Recommendation
from sqlalchemy import select

from horseracing_betting.exotic_backtest import run_exotic_backtest
from horseracing_betting.exotic_recommend import generate_exotic_recommendations
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    return make_prediction_run(session, race_id=_RACE, model_version=mv), mv


def test_real_odds_produce_real_roi_row_and_estimated_fallback(session, tmp_path):
    run_id, _ = _setup(session, tmp_path)
    # huge real trio dividend on [1,2,3] -> blended EV ranks it top, priced REAL
    session.add(ExoticOdds(race_id=_RACE, bet_type=BetType.TRIO, selection=[1, 2, 3],
                           odds=Decimal("500.0"), coverage_scope="partial", source="netkeiba"))
    session.commit()

    ids = generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0,
                                          top_k=3, use_real_odds=True)
    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    assert len(recs) == len(ids)

    real_rows = [r for r in recs if r.is_estimated_odds is False]
    assert real_rows, "the real-priced trio [1,2,3] must be recommended on real odds"
    trio = next(r for r in real_rows if r.bet_type == BetType.TRIO and r.selection == [1, 2, 3])
    assert trio.market_odds_used == Decimal("500.0")
    assert trio.estimated_market_odds_used is None

    # other selections without real odds fall back to estimated (double-pseudo)
    est_rows = [r for r in recs if r.is_estimated_odds is True]
    assert est_rows and all(r.market_odds_used is None for r in est_rows)
    assert all(r.estimated_market_odds_used is not None for r in est_rows)


def test_no_real_odds_is_all_estimated(session, tmp_path):
    run_id, _ = _setup(session, tmp_path)
    ids = generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0,
                                          top_k=2, use_real_odds=True)
    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    assert ids and all(r.is_estimated_odds is True for r in recs)  # no exotic_odds -> 011 estimated


def test_backtest_runs_with_real_odds_present(session, tmp_path):
    _, mv = _setup(session, tmp_path)
    session.add(ExoticOdds(race_id=_RACE, bet_type=BetType.TRIO, selection=[1, 2, 3],
                           odds=Decimal("40.0"), coverage_scope="partial", source="netkeiba"))
    session.commit()
    reports = run_exotic_backtest(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        threshold=0.0, top_k=2, stake=100.0, model_version=mv,
    )
    assert set(reports) == {"ev", "lowest_oest", "uniform"}
    # reports carry pseudo labels; with real odds on some selections a bet type may be real ROI
    assert "__total__" in reports["ev"]
