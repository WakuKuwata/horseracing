"""T037: stale RUNNING recovery — re-queue under max_retry, FAILED when exhausted."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob

from horseracing_ops.worker import recover_stale
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _running(session, *, retry_count: int, started_minutes_ago: int) -> IngestionJob:
    job = IngestionJob(
        source=Source.NETKEIBA, job_type="refresh_race", scope="race", scope_value=RID,
        status=JobStatus.RUNNING, retry_count=retry_count, max_retry=5,
        started_at=datetime.datetime.now(datetime.UTC)
        - datetime.timedelta(minutes=started_minutes_ago),
    )
    session.add(job)
    session.commit()
    return job


def test_stale_requeued_under_max_retry(session):
    seed_race(session, race_id=RID)
    job = _running(session, retry_count=0, started_minutes_ago=60)
    n = recover_stale(session, stale_seconds=900)  # 15 min
    assert n == 1
    session.refresh(job)
    assert job.status == JobStatus.QUEUED and job.retry_count == 1 and job.started_at is None


def test_stale_failed_when_exhausted(session):
    seed_race(session, race_id=RID)
    job = _running(session, retry_count=5, started_minutes_ago=60)  # already at max_retry
    recover_stale(session, stale_seconds=900)
    session.refresh(job)
    assert job.status == JobStatus.FAILED and job.completed_at is not None


def test_fresh_running_untouched(session):
    seed_race(session, race_id=RID)
    job = _running(session, retry_count=0, started_minutes_ago=1)  # within window
    n = recover_stale(session, stale_seconds=900)
    assert n == 0
    session.refresh(job)
    assert job.status == JobStatus.RUNNING