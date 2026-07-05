"""Feature 055: opt-in materialized feature reads in serving.

- run_serving(use_materialized=True) predictions are byte-identical to the in-memory path (the
  non-negotiable parity gate, on p itself).
- run_serving_backfill verifies the fingerprint ONCE up front (wiring), and a stale parquet aborts
  the run fail-closed (not swallowed into error_days).
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import RacePrediction
from horseracing_features.loader import load_frames
from horseracing_features.materialize import MaterializationError, write_materialized
from sqlalchemy import select

import horseracing_serving.pipeline as pipeline
from horseracing_serving.pipeline import run_serving, run_serving_backfill
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"           # race_date 2008-01-02 (seed_learnable r=1)
_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 1, 20)


def _win_probs(session, run_id) -> dict[str, float]:
    rows = session.scalars(
        select(RacePrediction).where(RacePrediction.prediction_run_id == run_id)
    ).all()
    return {r.horse_id: float(r.win_prob) for r in rows}


def _materialize(session, tmp_path):
    path = tmp_path / "features.parquet"
    write_materialized(path, load_frames(session))
    return path


def test_run_serving_materialized_p_parity(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    path = _materialize(session, tmp_path)

    # end_date (2008-01-02) < data_through (last seeded race) → the delta-verification path runs
    base = run_serving(session, race_id=_RACE, model_version=mv)
    mat = run_serving(
        session, race_id=_RACE, model_version=mv,
        use_materialized=True, materialized_path=str(path),
    )
    p0 = _win_probs(session, base[0].prediction_run_id)
    p1 = _win_probs(session, mat[0].prediction_run_id)
    assert p0 == p1  # byte-identical p (FR-001 / SC-002)


def test_backfill_materialized_verifies_once_and_matches(session, tmp_path, monkeypatch):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    path = _materialize(session, tmp_path)

    calls = {"n": 0}
    real = pipeline.verify_materialized

    def counting(sess, p):
        calls["n"] += 1
        return real(sess, p)

    monkeypatch.setattr(pipeline, "verify_materialized", counting)
    counts = run_serving_backfill(
        session, date_from=_FROM, date_to=_TO, model_version=mv,
        use_materialized=True, materialized_path=str(path),
    )
    assert calls["n"] == 1                      # verify-once (D3), not per day
    assert counts.generated == 10 and counts.error_days == 0

    # parity vs the in-memory backfill (force → fresh runs for the same model)
    run_serving_backfill(session, date_from=_FROM, date_to=_TO, model_version=mv, force=True)
    # both paths persisted runs for _RACE; compare the two most recent runs' p maps
    from horseracing_db.models import PredictionRun
    runs = session.scalars(
        select(PredictionRun).where(PredictionRun.race_id == _RACE)
        .where(PredictionRun.model_version == mv).order_by(PredictionRun.computed_at)
    ).all()
    assert len(runs) >= 2
    assert _win_probs(session, runs[-1].prediction_run_id) == _win_probs(
        session, runs[-2].prediction_run_id
    )


def test_backfill_stale_parquet_aborts_fail_closed(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    path = _materialize(session, tmp_path)

    # in-range source change AFTER materialize → the up-front verification must abort the run
    from horseracing_db.models import RaceResult
    row = session.scalars(select(RaceResult).where(RaceResult.race_id == _RACE)).first()
    row.last_3f = 99.9
    session.flush()

    with pytest.raises(MaterializationError):
        run_serving_backfill(
            session, date_from=_FROM, date_to=_TO, model_version=mv,
            use_materialized=True, materialized_path=str(path),
        )
