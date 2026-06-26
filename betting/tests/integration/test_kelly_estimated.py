"""T015 (US3): estimated-odds Kelly is double-pseudo labelled and conservatively suppressed (016).

Covers SC-004/SC-005. Real odds are injected equal to the estimated O_est so the SAME bet can be
priced via both paths — isolating the λ_real vs λ_est conservatism (caps raised so they don't mask
it). enable_estimated=False must yield zero bets.
"""

from __future__ import annotations

import pytest
from horseracing_db.models import ExoticOdds, Recommendation
from sqlalchemy import select

from horseracing_betting.exotic_ev import candidate_bets, canonical_field
from horseracing_betting.exotic_recommend import _load_field_inputs
from horseracing_betting.kelly_recommend import generate_kelly_recommendations
from horseracing_betting.kelly_types import KellyConfig
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_NO_TAKEOUT = None  # estimate with default takeout for estimated path; real injected = O_est below
# caps raised so the λ_real/λ_est ratio is visible (not clipped); heuristic = per-bet λ·f*.
_CFG = KellyConfig(lambda_real=0.50, lambda_est=0.10, cap_bet=1.0, cap_total=1.0, o_min=1.0,
                   min_edge=0.0, min_edge_est=0.0, bankroll=100.0, allocation="heuristic")
_RATES = {bt: 1.0 for bt in
          ("place", "quinella", "exacta", "wide", "trio", "trifecta")}  # no-takeout → +edge exists


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)
    # inject real odds EQUAL to the estimated O_est (no-takeout) so both paths price identically.
    preds, odds, scr, n2id = _load_field_inputs(session, run_id, _RACE)
    field = canonical_field(_RACE, preds, odds, scratched=scr, number_to_id=n2id)
    for bets in candidate_bets(field, payout_rates=_RATES).values():
        for b in bets:
            session.add(ExoticOdds(race_id=_RACE, bet_type=b.bet_type, selection=list(b.selection),
                                   odds=round(b.o_est, 6), coverage_scope="full"))
    session.commit()
    return run_id


def _fracs(session, ids):
    rs = [r for r in session.scalars(select(Recommendation)).all()
          if r.recommendation_id in set(ids)]
    return {(r.bet_type, tuple(r.selection)): r for r in rs}


def test_estimated_path_is_double_pseudo_labelled(session, tmp_path):
    run_id = _setup(session, tmp_path)
    ids = generate_kelly_recommendations(
        session, prediction_run_id=run_id, cfg=_CFG, threshold=1.0, top_k=3,
        payout_rates=_RATES, use_real_odds=False,   # force estimated path
    )
    assert len(ids) >= 1
    for r in _fracs(session, ids).values():
        assert r.is_estimated_odds is True          # double_pseudo (API derives the flag) — SC-004
        assert r.market_odds_used is None
        assert r.estimated_market_odds_used is not None
        assert r.stake_fraction is not None and float(r.stake_fraction) > 0.0


def test_estimated_more_conservative_than_real(session, tmp_path):
    run_id = _setup(session, tmp_path)
    real = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                          threshold=1.0, top_k=3, payout_rates=_RATES,
                                          use_real_odds=True)
    est = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                         threshold=1.0, top_k=3, payout_rates=_RATES,
                                         use_real_odds=False)
    rmap, emap = _fracs(session, real), _fracs(session, est)
    common = set(rmap) & set(emap)
    assert common  # same selections priced both ways
    for key in common:
        # same bet, estimated uses λ_est(0.10) < λ_real(0.50) → smaller stake (SC-005)
        assert float(emap[key].stake_fraction) <= float(rmap[key].stake_fraction) + 1e-12
        assert float(emap[key].stake_fraction) < float(rmap[key].stake_fraction)


def test_enable_estimated_false_yields_zero(session, tmp_path):
    run_id = _setup(session, tmp_path)
    cfg = KellyConfig(**{**_CFG.__dict__, "enable_estimated": False})
    ids = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=cfg,
                                         threshold=1.0, top_k=3, payout_rates=_RATES,
                                         use_real_odds=False)
    assert ids == []   # estimated disabled → no Kelly bets on the estimated path
