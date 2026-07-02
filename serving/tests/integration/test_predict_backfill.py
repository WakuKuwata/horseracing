"""Feature 044: run_serving_backfill — p-parity with per-day run_serving, idempotent per model
(force regenerates), reconciliation counts, per-day exception isolation.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import PredictionRun, RacePrediction
from sqlalchemy import func, select

from horseracing_serving.pipeline import run_serving, run_serving_backfill
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"           # race_date 2008-01-02 (seed_learnable r=1)
_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 1, 20)  # covers all 2008 races (2008-01-02 .. 2008-01-11)


def _run_count(session, model_version) -> int:
    return session.scalar(
        select(func.count()).select_from(PredictionRun)
        .where(PredictionRun.model_version == model_version)
    )


def _win_probs(session, run_id) -> dict[str, float]:
    rows = session.scalars(
        select(RacePrediction).where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {r.horse_id: float(r.win_prob) for r in rows}


def test_backfill_generates_and_is_idempotent(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    c1 = run_serving_backfill(session, date_from=_FROM, date_to=_TO, model_version=mv)
    assert c1.generated == 10 and c1.skip_exists == 0   # all 10 2008 races generated
    assert _run_count(session, mv) == 10

    # second run → idempotent: all skipped (model already has a run per race), no new runs
    c2 = run_serving_backfill(session, date_from=_FROM, date_to=_TO, model_version=mv)
    assert c2.generated == 0 and c2.skip_exists == 10
    assert _run_count(session, mv) == 10

    # force → regenerates (append-only, new runs)
    c3 = run_serving_backfill(session, date_from=_FROM, date_to=_TO, model_version=mv, force=True)
    assert c3.generated == 10
    assert _run_count(session, mv) == 20


def test_backfill_p_parity_with_run_serving(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)

    run_serving_backfill(session, date_from=_FROM, date_to=_TO, model_version=mv)
    bf_run = session.scalars(
        select(PredictionRun.prediction_run_id)
        .where(PredictionRun.race_id == _RACE).where(PredictionRun.model_version == mv)
    ).first()
    bf = _win_probs(session, bf_run)

    # a fresh per-race run_serving (always regenerates) must give byte-identical win_prob (p-parity)
    rs_run = run_serving(session, race_id=_RACE, model_version=mv)[0].prediction_run_id
    rs = _win_probs(session, rs_run)

    assert bf.keys() == rs.keys()
    for hid in bf:
        assert bf[hid] == rs[hid]  # byte-identical (same end_date=day matrix + _predict_persist)


def test_backfill_out_of_range_untouched(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    # backfill only a 2008 sub-window that contains NO race (2008-01-15..01-16)
    c = run_serving_backfill(
        session, date_from=datetime.date(2008, 1, 15), date_to=datetime.date(2008, 1, 16),
        model_version=mv,
    )
    assert c.generated == 0 and _run_count(session, mv) == 0
