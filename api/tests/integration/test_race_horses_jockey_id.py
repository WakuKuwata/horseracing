"""T016 (US2): /races/{id} HorseEntry exposes jockey_id/trainer_id (link contract, FR-010)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, Trainer

pytestmark = pytest.mark.integration


def test_horse_entry_includes_ids(client, session):
    session.merge(Race(race_id="200806010101", race_number=1,
                       race_date=datetime.date(2008, 6, 1), venue_code="05"))
    session.merge(Horse(horse_id="H1", horse_name="H1"))
    session.merge(Jockey(jockey_id="J1", jockey_name="騎手1"))
    session.merge(Trainer(trainer_id="T1", trainer_name="調教師1"))
    session.flush()
    session.merge(RaceHorse(race_id="200806010101", horse_id="H1", horse_number=1,
                            jockey_id="J1", trainer_id="T1", entry_status=EntryStatus.STARTED))
    session.commit()

    b = client.get("/api/v1/races/200806010101").json()
    entry = b["horses"][0]
    assert entry["jockey_id"] == "J1" and entry["trainer_id"] == "T1"
    assert entry["jockey_name"] == "騎手1"
