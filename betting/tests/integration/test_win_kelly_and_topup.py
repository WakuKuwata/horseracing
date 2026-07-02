"""Feature 045: (a) 007 Kelly opt-in — cfg gives win rows a stake_fraction, cfg=None stays flat;
(b) group-wise idempotency — an exotic-only (043-era) run gets win topped up without duplicating.
"""

from __future__ import annotations

import argparse

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation
from sqlalchemy import func, select

from horseracing_betting.cli import _cmd_recommend_serve
from horseracing_betting.kelly_recommend import generate_kelly_recommendations
from horseracing_betting.kelly_types import KellyConfig
from horseracing_betting.recommend import generate_recommendations
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    return make_prediction_run(session, race_id=_RACE, model_version=mv)


def _count(session, run_id, bet_types) -> int:
    return session.scalar(
        select(func.count()).select_from(Recommendation)
        .where(Recommendation.prediction_run_id == run_id)
        .where(Recommendation.bet_type.in_(bet_types))
    )


def test_win_kelly_opt_in_and_flat_backcompat(session, tmp_path):
    run_id = _setup(session, tmp_path)
    # flat (cfg=None): original behaviour — NULL stake_fraction
    ids_flat = generate_recommendations(session, prediction_run_id=run_id, threshold=1.0)
    flat = session.scalars(select(Recommendation)
                           .where(Recommendation.recommendation_id.in_(ids_flat))).all()
    assert flat and all(r.stake_fraction is None for r in flat)
    assert all(r.is_estimated_odds is False for r in flat)  # real win odds

    # Kelly opt-in: at least one positive-edge bet gets a stake; lv records the kelly cfg
    cfg = KellyConfig(lambda_real=0.5, min_edge=0.0)
    ids_k = generate_recommendations(session, prediction_run_id=run_id, threshold=1.0, cfg=cfg)
    kelly = session.scalars(select(Recommendation)
                            .where(Recommendation.recommendation_id.in_(ids_k))).all()
    assert kelly
    assert any(r.stake_fraction is not None and float(r.stake_fraction) > 0 for r in kelly)
    assert all(";kelly=" in r.logic_version for r in kelly)


def test_serve_tops_up_win_on_exotic_only_run(session, tmp_path, capsys):
    run_id = _setup(session, tmp_path)
    # simulate a 043-era run: exotic set only
    generate_kelly_recommendations(session, prediction_run_id=run_id, cfg=KellyConfig())
    n_exotic_before = _count(session, run_id, BetType.EXOTIC)
    assert _count(session, run_id, (BetType.WIN,)) == 0

    rc = _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE))
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("OK:") and "skipped groups: exotic" in out
    assert _count(session, run_id, (BetType.WIN,)) > 0                     # win topped up
    assert _count(session, run_id, BetType.EXOTIC) == n_exotic_before      # exotic NOT duplicated

    # third run → both groups present → full skip
    rc2 = _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE))
    assert rc2 == 0 and "SKIPPED" in capsys.readouterr().out
