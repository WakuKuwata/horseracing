"""migration 0004: ingestion_jobs audit counts + skipped status."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob

pytestmark = pytest.mark.integration


def test_skipped_status_accepted(session):
    job = IngestionJob(source=Source.JRA_VAN, scope="year", scope_value="2006",
                       status=JobStatus.SKIPPED, skipped_rows=12345)
    session.add(job)
    session.flush()
    session.refresh(job)
    assert job.status == "skipped"
    assert job.skipped_rows == 12345


def test_count_columns_and_summary(session):
    job = IngestionJob(
        source=Source.JRA_VAN, scope="year", scope_value="2007",
        status=JobStatus.SUCCEEDED,
        processed_rows=49009, skipped_rows=0, error_count=3,
        summary={"races": 3400, "race_horses": 49000, "race_results": 48500},
    )
    session.add(job)
    session.flush()
    session.refresh(job)
    assert job.processed_rows == 49009
    assert job.error_count == 3
    assert job.summary["races"] == 3400


def test_invalid_status_still_rejected(session):
    session.add(IngestionJob(source=Source.JRA_VAN, status="bogus"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
