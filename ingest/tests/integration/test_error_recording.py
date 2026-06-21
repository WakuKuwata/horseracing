"""Polish (SC-005): malformed rows are recorded in ingestion_jobs, not dropped."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob, RaceHorse
from sqlalchemy import select

from horseracing_ingest.pipeline import ingest_year
from tests._sjis import make_row, write_csv

pytestmark = pytest.mark.integration

RACE_ID = "200701010101"


def test_bad_rows_recorded_good_row_survives(session, tmp_path):
    good = make_row(horse_id="H1", horse_number="1", finish_order="1")
    bad_venue = make_row(horse_id="H2", horse_number="2", venue="大井")  # unknown venue
    bad_status = make_row(horse_id="H3", horse_number="3", finish_order="ZZ")  # unknown status

    p = tmp_path / "2007"
    # Build a file mixing a valid CSV row, a short row, an undecodable line, and 2 bad-mapping rows.
    write_csv(p, [good])
    with open(p, "ab") as f:
        f.write("a,b,c\n".encode("cp932"))  # wrong column count
        f.write(b"\x81\xffundecodable\n")  # cp932 decode error (lead + invalid trail)
        f.write((",".join(bad_venue) + "\n").encode("cp932"))
        f.write((",".join(bad_status) + "\n").encode("cp932"))

    summary = ingest_year(session, p)

    assert summary.errors >= 4  # short, decode, venue, status
    job = session.scalars(
        select(IngestionJob).where(IngestionJob.scope_value == "2007")
    ).first()
    assert job.error_count >= 4
    assert job.error_message  # line-numbered reasons retained

    # the one good row still made it in
    assert session.get(RaceHorse, (RACE_ID, "H1")) is not None
