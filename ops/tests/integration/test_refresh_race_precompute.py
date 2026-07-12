"""Precompute: a successful refresh_race (entries landed) chases the race with a predict job so the
detail page reads a persisted run instead of blocking on the ~1-minute on-demand build. Guarded on
"active model has no run yet" so repeated odds refreshes don't re-predict."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob, ModelVersion, PredictionRun
from sqlalchemy import func, select

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _seed_active_model(session, mv="m-active"):
    session.add(ModelVersion(model_version=mv, adoption_status="active"))
    session.flush()
    return mv


def _queued_predicts(session, race_id):
    return session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == "predict")
        .where(IngestionJob.scope_value == race_id)
    ).all()


def test_refresh_enqueues_predict_when_no_active_run(session, fixture_fetcher):
    seed_race(session, race_id=RID)
    _seed_active_model(session)
    job, _ = enqueue_race(session, RID)
    session.commit()

    drain(session, fetcher=fixture_fetcher, max_jobs=1)  # run ONLY the refresh_race

    session.refresh(job)
    assert job.status in ("succeeded", "partial")
    assert "predict_job_id" in job.summary  # follow-up recorded
    predicts = _queued_predicts(session, RID)
    assert len(predicts) == 1 and predicts[0].status == "queued"


def test_refresh_skips_predict_when_active_run_exists(session, fixture_fetcher):
    seed_race(session, race_id=RID)
    mv = _seed_active_model(session)
    session.add(  # already predicted by active model
        PredictionRun(race_id=RID, model_version=mv, logic_version="lv-test")
    )
    job, _ = enqueue_race(session, RID)
    session.commit()

    drain(session, fetcher=fixture_fetcher, max_jobs=1)

    session.refresh(job)
    assert job.status in ("succeeded", "partial")
    assert "predict_job_id" not in job.summary
    assert _queued_predicts(session, RID) == []


def test_refresh_skips_predict_when_no_active_model(session, fixture_fetcher):
    # no active model seeded -> nothing meaningful to precompute; don't spawn a doomed predict.
    seed_race(session, race_id=RID)
    job, _ = enqueue_race(session, RID)
    session.commit()

    drain(session, fetcher=fixture_fetcher, max_jobs=1)

    session.refresh(job)
    assert "predict_job_id" not in job.summary
    assert (
        session.scalar(
            select(func.count()).select_from(IngestionJob)
            .where(IngestionJob.job_type == "predict")
        )
        == 0
    )
