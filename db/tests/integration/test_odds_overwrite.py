"""US2 / INV-4: odds overwrite keeps a single row and advances updated_at (trigger)."""

from __future__ import annotations

import datetime
import time

import pytest
from sqlalchemy import select

from horseracing_db.models import Horse, Race, RaceHorse

pytestmark = pytest.mark.integration

RACE_ID = "202705021101"


def test_odds_overwrite_no_history(session):
    session.add(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2027, 5, 1)))
    session.add(Horse(horse_id="H1"))
    session.add(RaceHorse(race_id=RACE_ID, horse_id="H1", odds=3.0))
    session.commit()

    rh = session.get(RaceHorse, (RACE_ID, "H1"))
    before = rh.updated_at

    time.sleep(0.05)
    rh.odds = 9.9
    session.commit()
    session.refresh(rh)

    rows = session.execute(
        select(RaceHorse).where(RaceHorse.race_id == RACE_ID, RaceHorse.horse_id == "H1")
    ).scalars().all()
    assert len(rows) == 1, "odds update must not create a history row"
    assert float(rows[0].odds) == 9.9
    assert rows[0].updated_at > before, "updated_at trigger must advance on UPDATE"
