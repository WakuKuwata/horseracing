"""US3 / FR-016: ingestion_jobs status CHECK and failure recording."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob

pytestmark = pytest.mark.integration


def test_partial_and_failed_records_error(session):
    job = IngestionJob(
        source=Source.NETKEIBA,
        job_type="result",
        scope="race_id",
        scope_value="202705021101",
        status=JobStatus.PARTIAL,
        error_message="selector changed on 2 of 12 races",
    )
    session.add(job)
    session.flush()
    session.refresh(job)
    assert job.status == JobStatus.PARTIAL
    assert "selector changed" in job.error_message
    assert job.retry_count == 0
    assert job.max_retry == 5


def test_invalid_status_rejected(session):
    session.add(IngestionJob(source=Source.JRA_VAN, status="exploded"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
