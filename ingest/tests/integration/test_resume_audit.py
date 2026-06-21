"""US3: checkpoint resume produces no duplicates; counts recorded (SC-006 audit)."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob, RaceHorse
from sqlalchemy import func, select

from horseracing_ingest.pipeline import ingest_year
from tests._sjis import make_row, write_csv

pytestmark = pytest.mark.integration


def test_resume_no_duplicates_and_counts(session, tmp_path):
    rows = [
        make_row(horse_id=f"H{i}", horse_number=str(i), finish_order=str(i)) for i in range(1, 6)
    ]
    p = write_csv(tmp_path / "2007", rows)

    ingest_year(session, p, resume_from_line=2)  # process lines 3-5 only
    ingest_year(session, p)  # full run: lines 1-2 new, 3-5 upserted (no dup)

    assert session.scalar(select(func.count()).select_from(RaceHorse)) == 5

    jobs = session.scalars(
        select(IngestionJob).where(IngestionJob.scope_value == "2007")
    ).all()
    assert jobs
    assert all(j.processed_rows is not None for j in jobs)
    assert any(j.summary and j.summary.get("race_horses") for j in jobs)
