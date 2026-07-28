"""T030 (US3): freshness window + force — reuse a fresh success, re-fetch when stale or forced."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import IngestionJob
from sqlalchemy import select

from horseracing_ops.enqueue import enqueue_race
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _succeed(session, secs_ago: int):
    job, _ = enqueue_race(session, RID, origin="manual_ui")
    job.status = "succeeded"
    job.completed_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=secs_ago)
    session.commit()
    return job


def test_fresh_success_reused(session):
    seed_race(session, race_id=RID)
    done = _succeed(session, secs_ago=5)
    job, reused = enqueue_race(
        session,
        RID,
        origin="manual_ui",
        fresh_seconds=600,
    )
    session.commit()
    assert reused is True and job.ingestion_job_id == done.ingestion_job_id
    predict = session.scalar(
        select(IngestionJob)
        .where(IngestionJob.job_type == "predict")
        .where(IngestionJob.scope_value == RID)
    )
    assert predict is not None
    assert predict.summary["predict_origin"] == "manual_ui"


def test_stale_success_refetched(session):
    seed_race(session, race_id=RID)
    done = _succeed(session, secs_ago=5)
    job, reused = enqueue_race(
        session,
        RID,
        origin="manual_ui",
        fresh_seconds=1,
    )  # 5s > 1s window
    session.commit()
    assert reused is False and job.ingestion_job_id != done.ingestion_job_id


def test_force_ignores_freshness(session):
    seed_race(session, race_id=RID)
    done = _succeed(session, secs_ago=5)
    job, reused = enqueue_race(
        session,
        RID,
        origin="manual_ui",
        force=True,
        fresh_seconds=600,
    )
    session.commit()
    assert reused is False and job.ingestion_job_id != done.ingestion_job_id
