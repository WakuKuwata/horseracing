"""T013 (US1): generate_exotic_recommendations persists DOUBLE-pseudo exotic bets, leak-safe.

Covers SC-001/SC-003/SC-007 and the leak boundary (FR-004): mutating race_results must not change
the generated selections (generation never reads results).
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import RaceResult, Recommendation
from sqlalchemy import func, select

from horseracing_betting.exotic_recommend import generate_exotic_recommendations
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_EXOTIC = set(BetType.ALL) - {BetType.WIN}


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    return make_prediction_run(session, race_id=_RACE, model_version=mv)


def test_exotic_recommend_persists_double_pseudo_with_audit(session, tmp_path):
    run_id = _setup(session, tmp_path)
    ids = generate_exotic_recommendations(
        session, prediction_run_id=run_id, threshold=0.0, top_k=3
    )
    assert len(ids) >= 1

    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    assert len(recs) == len(ids)

    per_type: dict[str, int] = {}
    for rec in recs:
        assert rec.bet_type in _EXOTIC
        assert rec.race_id == _RACE
        # JSONB-safe array selection (NOT a dict/frozenset)
        assert isinstance(rec.selection, list)
        assert all(isinstance(x, int) for x in rec.selection)
        # DOUBLE-pseudo disclosure
        assert rec.is_estimated_odds is True
        assert rec.market_odds_used is None
        assert rec.estimated_market_odds_used is not None and rec.estimated_market_odds_used > 0
        assert rec.pseudo_odds is not None and rec.pseudo_roi is not None
        assert "exotic_ev=P_model(009;p)*odds[real_exotic>est_O_est(010;q)]" in rec.logic_version
        assert "qsrc=market_win_odds" in rec.logic_version
        per_type[rec.bet_type] = per_type.get(rec.bet_type, 0) + 1

    # top-K respected per bet type
    assert all(c <= 3 for c in per_type.values())


def test_exotic_recommend_selection_shapes(session, tmp_path):
    run_id = _setup(session, tmp_path)
    generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0, top_k=5)
    recs = session.scalars(select(Recommendation)).all()
    sizes = {
        BetType.PLACE: 1, BetType.QUINELLA: 2, BetType.EXACTA: 2,
        BetType.WIDE: 2, BetType.TRIO: 3, BetType.TRIFECTA: 3,
    }
    for rec in recs:
        assert len(rec.selection) == sizes[rec.bet_type]
        # unordered types are ascending-sorted
        if rec.bet_type in (BetType.QUINELLA, BetType.WIDE, BetType.TRIO):
            assert rec.selection == sorted(rec.selection)


def test_exotic_recommend_is_append_only(session, tmp_path):
    run_id = _setup(session, tmp_path)
    n1 = len(generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0, top_k=2))
    generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0, top_k=2)
    total = session.scalar(select(func.count()).select_from(Recommendation))
    assert total == 2 * n1  # appended, nothing overwritten


def test_exotic_recommend_is_leak_safe(session, tmp_path):
    """Mutating race_results must not change the generated selections (FR-004)."""
    run_id = _setup(session, tmp_path)
    first = generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0, top_k=4)
    sel_first = sorted(
        (r.bet_type, tuple(r.selection))
        for r in session.scalars(
            select(Recommendation).where(Recommendation.recommendation_id.in_(first))
        )
    )

    # scramble the finishing order — selection must be unaffected
    for res in session.scalars(select(RaceResult).where(RaceResult.race_id == _RACE)):
        res.finish_order = 9 - (res.finish_order or 1)
    session.commit()

    second = generate_exotic_recommendations(session, prediction_run_id=run_id, threshold=0.0, top_k=4)
    sel_second = sorted(
        (r.bet_type, tuple(r.selection))
        for r in session.scalars(
            select(Recommendation).where(Recommendation.recommendation_id.in_(second))
        )
    )
    assert sel_first == sel_second
