"""US1 (SC-001/SC-003) + FR-019: ingest one year, idempotent, Unknown != 0."""

from __future__ import annotations

import pytest
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import func, select

from horseracing_ingest.pipeline import ingest_year
from tests._sjis import make_row, write_csv

pytestmark = pytest.mark.integration

RACE_ID = "200701010101"


def _fixture(tmp_path):
    rows = [
        make_row(horse_id="H1", horse_number="1", frame="1", finish_order="1",
                 horse_weight="460", weight_diff="0"),
        make_row(horse_id="H2", horse_number="2", frame="2", finish_order="2",
                 horse_weight="", weight_diff=""),  # unweighed -> NULL
        make_row(horse_id="H3", horse_number="3", frame="3", finish_order="3"),
    ]
    return write_csv(tmp_path / "2007", rows)


def test_counts_and_idempotent(session, tmp_path):
    p = _fixture(tmp_path)
    s1 = ingest_year(session, p)
    assert (s1.races, s1.race_horses, s1.race_results) == (1, 3, 3)

    ingest_year(session, p)  # re-run
    assert session.scalar(select(func.count()).select_from(Race)) == 1
    assert session.scalar(select(func.count()).select_from(RaceHorse)) == 3


def test_missing_weight_is_null_not_zero(session, tmp_path):
    ingest_year(session, _fixture(tmp_path))
    assert session.get(RaceHorse, (RACE_ID, "H1")).weight == 460
    assert session.get(RaceHorse, (RACE_ID, "H2")).weight is None  # FR-019 / 憲法 IV
