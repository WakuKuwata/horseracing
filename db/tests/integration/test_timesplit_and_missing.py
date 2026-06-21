"""US2 / FR-008, FR-010: walk-forward time split and Unknown(null) vs 0."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import func, select

from horseracing_db.models import Horse, Race, RaceHorse

pytestmark = pytest.mark.integration


def test_time_split_excludes_on_or_after_cutoff(session):
    session.add(Race(race_id="202612310101", race_number=1, race_date=datetime.date(2026, 12, 31)))
    session.add(Race(race_id="202701010101", race_number=1, race_date=datetime.date(2027, 1, 1)))
    session.add(Race(race_id="202701020101", race_number=1, race_date=datetime.date(2027, 1, 2)))
    session.flush()

    cutoff = datetime.date(2027, 1, 1)
    before = session.execute(
        select(Race.race_id).where(Race.race_date < cutoff)
    ).scalars().all()
    assert before == ["202612310101"], "only races strictly before the cutoff are included"


def test_unknown_null_distinct_from_zero(session):
    session.add(Race(race_id="202705021101", race_number=11, race_date=datetime.date(2027, 5, 1)))
    session.add_all([Horse(horse_id="H1"), Horse(horse_id="H2")])
    session.add(RaceHorse(race_id="202705021101", horse_id="H1", weight=None))  # Unknown
    session.add(RaceHorse(race_id="202705021101", horse_id="H2", weight=0))  # actual zero
    session.flush()

    null_count = session.execute(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.weight.is_(None))
    ).scalar_one()
    zero_count = session.execute(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.weight == 0)
    ).scalar_one()
    assert null_count == 1
    assert zero_count == 1
