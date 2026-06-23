"""US1 (SC-001/002/005): generate_recommendations persists EV win bets with audit, append-only."""

from __future__ import annotations

import pytest
from horseracing_db.models import RaceHorse, Recommendation
from sqlalchemy import func, select

from horseracing_betting.recommend import generate_recommendations
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def test_recommend_persists_ev_win_bets_with_audit(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)

    ids = generate_recommendations(session, prediction_run_id=run_id, threshold=1.0, stake=100.0)
    assert len(ids) >= 1

    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    assert len(recs) == len(ids)
    odds_by_horse = dict(
        session.execute(
            select(RaceHorse.horse_id, RaceHorse.odds).where(RaceHorse.race_id == _RACE)
        ).all()
    )
    for rec in recs:
        assert rec.bet_type == "win"
        assert rec.race_id == _RACE
        assert set(rec.selection) == {"horse_id", "horse_number"}
        assert rec.is_estimated_odds is False
        assert rec.estimated_market_odds_used is None
        assert rec.market_odds_used == odds_by_horse[rec.selection["horse_id"]]
        assert rec.pseudo_odds is not None and rec.pseudo_roi is not None
        assert "thr=1.0" in rec.logic_version and "renorm=started" in rec.logic_version


def test_recommend_is_append_only(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)

    n1 = len(generate_recommendations(session, prediction_run_id=run_id, threshold=1.0, stake=100.0))
    generate_recommendations(session, prediction_run_id=run_id, threshold=1.0, stake=100.0)
    total = session.scalar(select(func.count()).select_from(Recommendation))
    assert total == 2 * n1  # re-run appended a new group, nothing overwritten
