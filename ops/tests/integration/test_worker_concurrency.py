"""T026 (US2/FR-016): concurrent drain processes every queued job exactly once (SKIP LOCKED)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain_concurrent
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID_OK = "202406050911"  # has fixtures
OTHERS = [f"2024060509{n:02d}" for n in (1, 2, 3, 4)]  # no fixtures -> fail, but still terminal


def test_concurrent_drain_no_double_processing(session, engine, fixture_fetcher):
    seed_race(session, race_id=RID_OK)
    for rid in OTHERS:
        seed_race(session, race_id=rid)
    for rid in [RID_OK, *OTHERS]:
        enqueue_race(session, rid)
    session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    processed = drain_concurrent(factory, max_workers=3, fetcher_factory=lambda: fixture_fetcher)

    assert processed == 5  # each queued job claimed exactly once across the 3 workers
    # nothing left queued/running; all terminal
    left = session.scalar(
        select(func.count()).select_from(IngestionJob)
        .where(IngestionJob.job_type == "refresh_race")
        .where(IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
    )
    assert left == 0
    ok = session.scalar(
        select(func.count()).select_from(IngestionJob)
        .where(IngestionJob.job_type == "refresh_race")
        .where(IngestionJob.status == JobStatus.SUCCEEDED)
    )
    assert ok == 1  # only the fixture-backed race succeeds
