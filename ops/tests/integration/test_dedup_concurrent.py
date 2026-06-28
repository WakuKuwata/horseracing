"""T013 (US1): race-level dedup — a second enqueue reuses the active job, no duplicate queued row."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob
from sqlalchemy import func, select

from horseracing_ops.enqueue import enqueue_race
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_active_job_is_reused(session):
    seed_race(session, race_id=RID)
    job1, reused1 = enqueue_race(session, RID)
    session.commit()
    job2, reused2 = enqueue_race(session, RID)
    session.commit()

    assert reused1 is False and reused2 is True
    assert job1.ingestion_job_id == job2.ingestion_job_id
    n = session.scalar(
        select(func.count()).select_from(IngestionJob)
        .where(IngestionJob.job_type == "refresh_race")
        .where(IngestionJob.scope_value == RID)
    )
    assert n == 1  # only one queued row despite two enqueue calls


def test_force_reuses_active_but_not_fresh(session):
    seed_race(session, race_id=RID)
    # mark a recently-succeeded job; without force it would be reused, with force a new one is made
    job, _ = enqueue_race(session, RID)
    job.status = "succeeded"
    import datetime
    job.completed_at = datetime.datetime.now(datetime.UTC)
    session.commit()

    _, reused_no_force = enqueue_race(session, RID)
    session.commit()
    assert reused_no_force is True  # fresh success reused

    _, reused_force = enqueue_race(session, RID, force=True)
    session.commit()
    assert reused_force is False  # force ignores freshness -> new job
