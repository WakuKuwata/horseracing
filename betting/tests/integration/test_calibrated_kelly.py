"""T017 (US2): model-p calibrator opt-in into Kelly recommendations (Feature 017, SC-010)."""

from __future__ import annotations

import pytest
from horseracing_db.models import ExoticOdds, Recommendation
from horseracing_probability.model_calibration import PCalibrator
from sqlalchemy import select

from horseracing_betting.exotic_ev import candidate_bets, canonical_field
from horseracing_betting.exotic_recommend import _load_field_inputs
from horseracing_betting.kelly_recommend import generate_kelly_recommendations
from horseracing_betting.kelly_types import KellyConfig
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_CFG = KellyConfig(lambda_real=0.5, cap_bet=0.05, cap_total=0.10, o_min=1.5, min_edge=0.0,
                   bankroll=100.0, allocation="exact")


def _calibrator(gamma: float) -> PCalibrator:
    return PCalibrator(method="power", params={"gamma": gamma}, train_window=None, n_races=0,
                       n_samples=0, prob_range=(0.0, 1.0), select="explicit",
                       base_model_version=None,
                       logic_version=f"pcal=power(p^gamma);gamma={gamma:.5f};select=explicit",
                       sufficient=True)


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)
    preds, odds, scr, n2id = _load_field_inputs(session, run_id, _RACE)
    field = canonical_field(_RACE, preds, odds, scratched=scr, number_to_id=n2id)
    for bets in candidate_bets(field).values():
        for b in bets:
            session.add(ExoticOdds(race_id=_RACE, bet_type=b.bet_type, selection=list(b.selection),
                                   odds=round(1.5 / b.p_model, 4), coverage_scope="full"))
    session.commit()
    return run_id


def _fracs(session, ids):
    rs = [r for r in session.scalars(select(Recommendation)).all()
          if r.recommendation_id in set(ids)]
    return {(r.bet_type, tuple(r.selection)): r for r in rs}


def test_calibrator_records_logic_version(session, tmp_path):
    run_id = _setup(session, tmp_path)
    ids = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                         threshold=1.0, top_k=3, use_real_odds=True,
                                         p_calibrator=_calibrator(0.6))
    assert len(ids) >= 1
    for r in _fracs(session, ids).values():
        assert "pcal=power(p^gamma);gamma=0.60000" in r.logic_version
        assert "kelly-v1" in r.logic_version   # both Kelly + calibration recorded (reproducible)


def test_backward_compatible_without_calibrator(session, tmp_path):
    run_id = _setup(session, tmp_path)
    base = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                          threshold=1.0, top_k=3, use_real_odds=True)
    none_map = _fracs(session, base)
    assert all("pcal=" not in r.logic_version for r in none_map.values())   # raw p path unchanged


def test_calibration_changes_stake_fraction(session, tmp_path):
    run_id = _setup(session, tmp_path)
    raw = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                         threshold=1.0, top_k=3, use_real_odds=True)
    cal = generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=_CFG,
                                         threshold=1.0, top_k=3, use_real_odds=True,
                                         p_calibrator=_calibrator(0.5))
    rmap, cmap = _fracs(session, raw), _fracs(session, cal)
    common = set(rmap) & set(cmap)
    assert common
    # calibration (gamma=0.5 softens p) shifts P_model' → different stake fractions
    assert any(float(rmap[k].stake_fraction) != float(cmap[k].stake_fraction) for k in common)
