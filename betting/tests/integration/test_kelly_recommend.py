"""T009 (US1): Kelly recommendations persist stake_fraction with caps + determinism (Feature 016).

Covers SC-001/SC-002/SC-003. Synthetic estimated odds carry takeout (edge rarely >0), so we inject
real exotic odds priced to give a positive edge — exercising the real-odds MVP path (is_estimated
False) and the per-(race,bet_type) cap_total allocation.
"""

from __future__ import annotations

import pytest
from horseracing_db.models import ExoticOdds, Recommendation
from sqlalchemy import func, select

from horseracing_betting.exotic_ev import candidate_bets, canonical_field
from horseracing_betting.exotic_recommend import _load_field_inputs
from horseracing_betting.kelly_recommend import generate_kelly_recommendations
from horseracing_betting.kelly_types import KellyConfig
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_CFG = KellyConfig(lambda_real=0.5, lambda_est=0.10, cap_bet=0.05, cap_total=0.10,
                   o_min=1.5, min_edge=0.0, bankroll=100.0, allocation="exact")


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)
    # inject real exotic odds priced so EV = P_model·O = 1.5 (edge 0.5 > 0) for every candidate.
    preds, odds, scr, n2id = _load_field_inputs(session, run_id, _RACE)
    field = canonical_field(_RACE, preds, odds, scratched=scr, number_to_id=n2id)
    for bets in candidate_bets(field).values():
        for b in bets:
            session.add(ExoticOdds(race_id=_RACE, bet_type=b.bet_type, selection=list(b.selection),
                                   odds=round(1.5 / b.p_model, 4), coverage_scope="full"))
    session.commit()
    return run_id


def test_kelly_persists_stake_fraction_real_path(session, tmp_path):
    run_id = _setup(session, tmp_path)
    ids = generate_kelly_recommendations(
        session, prediction_run_id=run_id, cfg=_CFG, threshold=1.0, top_k=3, use_real_odds=True
    )
    assert len(ids) >= 1
    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    assert len(recs) == len(ids)

    by_group: dict[tuple[str, str], float] = {}
    for r in recs:
        assert r.stake_fraction is not None
        f = float(r.stake_fraction)
        assert 0.0 < f <= _CFG.cap_bet + 1e-9          # per-bet cap (SC-001)
        assert float(r.pseudo_roi) > 0.0                # only positive-edge bets persisted
        assert r.is_estimated_odds is False             # real exotic odds path
        assert r.market_odds_used is not None
        assert r.estimated_market_odds_used is None
        assert "kelly-v1" in r.logic_version and "alloc=exact" in r.logic_version
        by_group[(r.race_id, r.bet_type)] = by_group.get((r.race_id, r.bet_type), 0.0) + f
    # per-(race,bet_type) total cap never exceeded (SC-002)
    assert all(total <= _CFG.cap_total + 1e-9 for total in by_group.values())


def test_kelly_determinism(session, tmp_path):
    run_id = _setup(session, tmp_path)
    a = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG, threshold=1.0,
                                       top_k=3, use_real_odds=True)
    b = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG, threshold=1.0,
                                       top_k=3, use_real_odds=True)
    recs = session.scalars(select(Recommendation)).all()
    # compare by (bet_type, selection) → stake_fraction across the two runs
    def keyed(ids):
        rs = [x for x in recs if x.recommendation_id in set(ids)]
        return {(x.bet_type, tuple(x.selection)): round(float(x.stake_fraction), 9) for x in rs}
    assert keyed(a) == keyed(b)                         # identical output (SC-003)
    assert len(a) == len(b)


def test_kelly_exact_vs_heuristic_differ(session, tmp_path):
    run_id = _setup(session, tmp_path)
    ex = generate_kelly_recommendations(
        session, prediction_run_id=run_id,
        cfg=KellyConfig(**{**_CFG.__dict__, "allocation": "exact"}),
        threshold=1.0, top_k=5, use_real_odds=True)
    he = generate_kelly_recommendations(
        session, prediction_run_id=run_id,
        cfg=KellyConfig(**{**_CFG.__dict__, "allocation": "heuristic"}),
        threshold=1.0, top_k=5, use_real_odds=True)
    recs = session.scalars(select(Recommendation)).all()

    def fracs(ids):
        rs = [x for x in recs if x.recommendation_id in set(ids)]
        return {(x.bet_type, tuple(x.selection)): round(float(x.stake_fraction), 6) for x in rs}
    # with multiple mutually-exclusive bets per type, joint vs independent sizing differs (FR-004)
    assert fracs(ex) != fracs(he)


def test_kelly_negative_edge_not_persisted(session, tmp_path):
    run_id = _setup(session, tmp_path)
    # threshold 5.0 → EV≥5 required; none qualify → zero bets (negative-edge filter, no rows)
    ids = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                         threshold=5.0, top_k=3, use_real_odds=True)
    assert ids == []
    assert session.scalar(select(func.count()).select_from(Recommendation)) == 0
