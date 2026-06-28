"""T031 (US3): each refresh records an auditable ingestion_jobs row (FR-017, SC-007)."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob
from sqlalchemy import select

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_refresh_job_audit_fields(session, fixture_fetcher):
    seed_race(session, race_id=RID)
    job, _ = enqueue_race(session, RID)
    session.commit()
    drain(session, fetcher=fixture_fetcher)
    session.refresh(job)

    # the refresh_race orchestration row carries scope, terminal status, counts, summary, timestamps
    assert job.job_type == "refresh_race" and job.scope_value == RID
    assert job.status == "succeeded"
    assert job.processed_rows is not None and job.error_count == 0
    assert job.summary["kind"] == "entries+results+odds"
    assert job.started_at is not None and job.completed_at is not None

    # the underlying scrape calls are independently audited too (entries + results + odds)
    scrape_types = set(session.scalars(
        select(IngestionJob.job_type).where(
            IngestionJob.job_type.in_(["entries", "results", "odds"]))
    ))
    assert {"entries", "results", "odds"} <= scrape_types
