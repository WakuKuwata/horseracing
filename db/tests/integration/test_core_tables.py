"""US1: core tables exist, composite PK, upsert (no duplicate rows)."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import inspect, select

from horseracing_db.models import Horse, Race, RaceHorse

pytestmark = pytest.mark.integration

CORE_TABLES = {"races", "horses", "jockeys", "trainers", "race_horses", "race_results"}


def test_core_tables_exist(engine):
    names = set(inspect(engine).get_table_names())
    assert CORE_TABLES.issubset(names)


def test_composite_pk_upsert_no_duplicates(session):
    session.add(Race(race_id="202705021101", race_number=11, race_date=datetime.date(2027, 5, 1)))
    session.add(Horse(horse_id="H0001", horse_name="Tester"))
    session.flush()

    session.merge(RaceHorse(race_id="202705021101", horse_id="H0001", horse_number=1, odds=3.5))
    session.flush()
    # Re-upsert the same (race_id, horse_id) with a new value.
    session.merge(RaceHorse(race_id="202705021101", horse_id="H0001", horse_number=1, odds=4.0))
    session.flush()

    rows = session.execute(
        select(RaceHorse).where(
            RaceHorse.race_id == "202705021101", RaceHorse.horse_id == "H0001"
        )
    ).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].odds) == 4.0
