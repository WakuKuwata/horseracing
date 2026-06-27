"""T013 (US3): leak boundary (results ignored) + determinism (Feature 019, SC-005/SC-007)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_serving.pipeline import run_serving
from sqlalchemy import select
from horseracing_db.models import RacePrediction

from horseracing_live import live_serve
from tests._synth import add_results, make_active_model, seed_learnable, seed_pending_race

pytestmark = pytest.mark.integration

_PENDING = "200806019903"


def _winprobs(session, run_id) -> dict:
    rows = session.execute(
        select(RacePrediction.horse_id, RacePrediction.win_prob)
        .where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {hid: round(float(wp), 12) for hid, wp in rows}


def test_features_ignore_race_own_results(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))

    before = _winprobs(session, live_serve(session, race_id=_PENDING, model_version=mv,
                                           recommend=False).prediction_run_id)
    # add the race's OWN results, then predict again (bypass pending guard via run_serving):
    add_results(session, race_id=_PENDING)
    after = _winprobs(session, run_serving(session, race_id=_PENDING, model_version=mv)[0]
                      .prediction_run_id)
    assert before == after          # features never read the race's own results (leak boundary)


def test_deterministic_repeat(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))
    a = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    b = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    assert _winprobs(session, a.prediction_run_id) == _winprobs(session, b.prediction_run_id)
