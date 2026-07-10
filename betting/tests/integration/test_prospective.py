"""Feature 065: prospective marker on WIN recommendations — byte-identical off, marker on (with
odds_asof, surviving custom logic_version), and 4-policy idempotency (legacy/cap/prospective/
prospective+cap never collide, prospective is race-scoped across runs)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation
from sqlalchemy import select

from horseracing_betting.cli import _has_win_group
from horseracing_betting.recommend import generate_recommendations
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_ASOF = datetime.datetime(2008, 1, 1, 9, 30, tzinfo=datetime.UTC)


def _run(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    return make_prediction_run(session, race_id=_RACE, model_version=mv)


def _win_lvs(session, run_id):
    return [
        r.logic_version for r in session.scalars(
            select(Recommendation).where(Recommendation.prediction_run_id == run_id)
            .where(Recommendation.bet_type == BetType.WIN)
        )
    ]


def test_prospective_off_is_byte_identical(session, tmp_path):
    run_id = _run(session, tmp_path)
    generate_recommendations(session, prediction_run_id=run_id, prospective=False)
    lvs = _win_lvs(session, run_id)
    assert lvs                                        # some win bets generated
    assert all(";prospective=1" not in lv for lv in lvs)   # off ⇒ no marker (byte-identical)


def test_prospective_on_marks_with_odds_asof(session, tmp_path):
    run_id = _run(session, tmp_path)
    generate_recommendations(session, prediction_run_id=run_id, prospective=True, odds_asof=_ASOF)
    lvs = _win_lvs(session, run_id)
    assert lvs and all(";prospective=1;odds_asof=2008-01-01T09:30:00+00:00" in lv for lv in lvs)


def test_prospective_marker_survives_custom_logic(session, tmp_path):
    run_id = _run(session, tmp_path)
    generate_recommendations(session, prediction_run_id=run_id, logic_version="custom-lv",
                             prospective=True, odds_asof=_ASOF)
    lvs = _win_lvs(session, run_id)
    # custom logic kept AND marker appended after resolution (never dropped)
    assert lvs and all(lv.startswith("custom-lv;prospective=1;odds_asof=") for lv in lvs)


def test_has_win_group_distinguishes_four_policies(session, tmp_path):
    run_id = _run(session, tmp_path)
    # legacy (no cap, no prospective)
    generate_recommendations(session, prediction_run_id=run_id)
    assert _has_win_group(session, run_id, None) is True                 # legacy present
    assert _has_win_group(session, run_id, 21.0) is False                # cap policy absent
    assert _has_win_group(session, run_id, None, prospective=True, race_id=_RACE) is False

    # add prospective (no cap) — race-scoped
    generate_recommendations(session, prediction_run_id=run_id, prospective=True, odds_asof=_ASOF)
    assert _has_win_group(session, run_id, None, prospective=True, race_id=_RACE) is True
    # legacy check still true and NOT confused by the prospective rows
    assert _has_win_group(session, run_id, None) is True
    # cap + prospective still distinct/absent
    assert _has_win_group(session, run_id, 21.0, prospective=True, race_id=_RACE) is False
