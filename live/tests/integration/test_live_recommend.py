"""T010 (US2): live recommendations on pre-race odds (Feature 019, SC-002/SC-003, FR-010)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_betting.exotic_ev import canonical_field, candidate_bets
from horseracing_betting.exotic_recommend import _load_field_inputs
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ExoticOdds, RaceHorse, Recommendation
from sqlalchemy import select

from horseracing_live import live_serve
from tests._synth import make_active_model, seed_learnable, seed_pending_race

pytestmark = pytest.mark.integration

_PENDING = "200806019901"   # last 2 digits = race_number (must be 1–12)


def _setup_model(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    return make_active_model(session, tmp_path)


def _inject_real_exotic(session, run_id, race_id):
    """Price real exotic odds (EV≈1.5) for every candidate so Kelly yields recs deterministically."""
    preds, odds, scr, n2id = _load_field_inputs(session, run_id, race_id)
    field = canonical_field(race_id, preds, odds, scratched=scr, number_to_id=n2id)
    for bets in candidate_bets(field).values():
        for b in bets:
            session.add(ExoticOdds(race_id=race_id, bet_type=b.bet_type,
                                   selection=list(b.selection), odds=round(1.5 / b.p_model, 4),
                                   coverage_scope="full"))
    session.commit()


def test_live_recommend_saves_used_odds_and_shadow(session, tmp_path):
    mv = _setup_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1), with_odds=True)
    # first predict to get a run, then price real exotic odds, then serve with recommend
    r0 = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    _inject_real_exotic(session, r0.prediction_run_id, _PENDING)

    rep = live_serve(session, race_id=_PENDING, model_version=mv, recommend=True, top_k=3)
    assert rep.rejected is False and rep.n_recommendations >= 1
    assert rep.shadow is True                    # live Kelly = shadow (no real stakes)
    assert rep.odds_as_of is not None            # pre-race odds as_of recorded
    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == rep.prediction_run_id)
    ).all()
    assert len(recs) == rep.n_recommendations
    for r in recs:
        # used odds value persisted (not just as_of) — codex F-E/FR-008
        used = r.market_odds_used if r.market_odds_used is not None else r.estimated_market_odds_used
        assert used is not None and float(used) > 0
        assert r.stake_fraction is not None      # Kelly fraction recorded


def test_odds_missing_skips_recommend_keeps_prediction(session, tmp_path):
    mv = _setup_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1),
                      with_odds=False)            # no pre-race odds
    rep = live_serve(session, race_id=_PENDING, model_version=mv, recommend=True)
    assert rep.rejected is False                  # prediction still produced
    assert rep.prediction_run_id is not None
    assert rep.n_recommendations == 0             # odds-dependent recs skipped (SC-003)
    assert rep.recommend_skipped_reason and "odds" in rep.recommend_skipped_reason.lower()


def test_post_recommendation_scratch_excluded_on_reserve(session, tmp_path):
    mv = _setup_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1), with_odds=True)
    r0 = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    _inject_real_exotic(session, r0.prediction_run_id, _PENDING)
    live_serve(session, race_id=_PENDING, model_version=mv, recommend=True, top_k=3)

    # scratch horse_number 1, re-serve → new recommendations must not include it (F2/FR-010)
    rh = session.query(RaceHorse).filter(
        RaceHorse.race_id == _PENDING, RaceHorse.horse_number == 1
    ).first()
    rh.entry_status = EntryStatus.CANCELLED   # 出走取消（non-starter）
    session.commit()
    rep2 = live_serve(session, race_id=_PENDING, model_version=mv, recommend=True, top_k=3)
    new_recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == rep2.prediction_run_id)
    ).all()
    assert all(1 not in r.selection for r in new_recs)   # scratched horse void/skip
