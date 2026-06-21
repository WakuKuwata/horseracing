"""US1: race_id format CHECK and race_number range CHECK reject invalid values."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from horseracing_db.models import Race

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("bad_race_id", ["12345", "1234567890123", "20270502110A", ""])
def test_race_id_format_rejected(session, bad_race_id):
    session.add(Race(race_id=bad_race_id, race_number=1, race_date=datetime.date(2027, 5, 1)))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("bad_number", [0, 13, -1])
def test_race_number_range_rejected(session, bad_number):
    session.add(
        Race(race_id="202705021101", race_number=bad_number, race_date=datetime.date(2027, 5, 1))
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_valid_race_accepted(session):
    session.add(Race(race_id="202705021101", race_number=11, race_date=datetime.date(2027, 5, 1)))
    session.flush()  # no error
