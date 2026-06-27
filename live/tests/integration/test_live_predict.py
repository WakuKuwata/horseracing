"""T007 (US1): live prediction + fail-closed guards (Feature 019, SC-001/SC-005/SC-006)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import RacePrediction
from sqlalchemy import func, select

from horseracing_live import live_serve
from tests._synth import add_results, make_active_model, seed_learnable, seed_pending_race

pytestmark = pytest.mark.integration

_PENDING = "200806019911"


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    return mv


def test_live_predict_persists_prediction_run(session, tmp_path):
    mv = _setup(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1),
                      field_size=8, extra_horse=True)  # includes a debut (no-history) horse
    rep = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    assert rep.rejected is False and rep.mode == "live"
    assert rep.prediction_run_id is not None
    # debut horse is INCLUDED in the field (Unknown features, not dropped) → all 9 predicted
    n_pred = session.scalar(
        select(func.count()).select_from(RacePrediction)
        .where(RacePrediction.prediction_run_id == rep.prediction_run_id)
    )
    assert n_pred == rep.n_horses == 9
    # win_prob sums ≈ 1 over the field (009/IV consistency held by run_serving)
    s = session.scalar(
        select(func.sum(RacePrediction.win_prob))
        .where(RacePrediction.prediction_run_id == rep.prediction_run_id)
    )
    assert abs(float(s) - 1.0) < 1e-6


def test_rejects_invalid_race_id(session, tmp_path):
    rep = live_serve(session, race_id="999", recommend=False)
    assert rep.rejected and "invalid race_id" in rep.reason


def test_rejects_already_run_race(session, tmp_path):
    mv = _setup(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=datetime.date(2008, 6, 1))
    add_results(session, race_id=_PENDING)              # now has results → not result-pending
    rep = live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    assert rep.rejected and "result-pending" in rep.reason
    assert rep.prediction_run_id is None                # fail-closed: nothing predicted


def test_rejects_incomplete_entries(session, tmp_path):
    mv = _setup(session, tmp_path)
    # race exists (result-pending) but no started horses → entries_complete fails
    from horseracing_db.models import Race
    session.add(Race(race_id="200806019912", race_number=12, race_date=datetime.date(2008, 6, 1),
                     venue_code="05"))
    session.commit()
    rep = live_serve(session, race_id="200806019912", model_version=mv, recommend=False)
    assert rep.rejected and "entries" in rep.reason.lower()
