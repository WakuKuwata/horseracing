"""US3 (SC-004): pre-2007 files are skipped and never enter core tables."""

from __future__ import annotations

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob, Race
from sqlalchemy import select

from horseracing_ingest.pipeline import ingest_year
from tests._sjis import make_row, write_csv

pytestmark = pytest.mark.integration


def test_pre_2007_skipped(session, tmp_path):
    p2006 = write_csv(tmp_path / "2006", [make_row(race_date="2006.12.31", horse_id="OLD")])
    p2007 = write_csv(tmp_path / "2007", [make_row(race_date="2007.1.5", horse_id="H1")])

    s6 = ingest_year(session, p2006)
    s7 = ingest_year(session, p2007)

    assert s6.skipped is True and s6.skipped_rows == 1
    assert s7.skipped is False and s7.races == 1

    # no 2006 data entered any core table
    race_ids = session.scalars(select(Race.race_id)).all()
    assert race_ids and all(not r.startswith("2006") for r in race_ids)

    # skip recorded as a job row with status 'skipped'
    skip_jobs = session.scalars(
        select(IngestionJob).where(IngestionJob.status == JobStatus.SKIPPED)
    ).all()
    assert len(skip_jobs) == 1
    assert skip_jobs[0].scope_value == "2006"
