"""T024 (Polish): profile aggregate / history edge cases."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

pytestmark = pytest.mark.integration


def test_all_cancelled_horse_has_zero_starts_but_history(client, session):
    session.merge(Horse(horse_id="HC", horse_name="HC"))
    session.flush()
    for i in (1, 2):
        session.merge(Race(race_id=f"2008060{i}0101", race_number=1,
                           race_date=datetime.date(2008, 6, i), venue_code="05"))
        session.flush()
        session.merge(RaceHorse(race_id=f"2008060{i}0101", horse_id="HC", horse_number=1,
                                entry_status=EntryStatus.CANCELLED))
    session.commit()

    prof = client.get("/api/v1/horses/HC").json()
    assert prof["starts"] == 0 and prof["win_rate"] is None
    # history still lists the (cancelled) entries
    hist = client.get("/api/v1/horses/HC/history").json()
    assert hist["total"] == 2 and hist["items"][0]["entry_status"] == "cancelled"


def test_dead_heat_counts_both_as_first(client, session):
    # two horses dead-heat for 1st (finish_order=1 both) — each horse's career counts its own win
    session.merge(Race(race_id="200806010101", race_number=1,
                       race_date=datetime.date(2008, 6, 1), venue_code="05"))
    for hid in ("HA", "HB"):
        session.merge(Horse(horse_id=hid, horse_name=hid))
    session.flush()
    for hid in ("HA", "HB"):
        session.merge(RaceHorse(race_id="200806010101", horse_id=hid, horse_number=1,
                                entry_status=EntryStatus.STARTED))
        session.merge(RaceResult(race_id="200806010101", horse_id=hid, finish_order=1,
                                 result_status=ResultStatus.FINISHED))
    session.commit()

    assert client.get("/api/v1/horses/HA").json()["wins"] == 1
    assert client.get("/api/v1/horses/HB").json()["wins"] == 1


def test_history_page_beyond_range_is_empty(client, session):
    session.merge(Horse(horse_id="H1", horse_name="H1"))
    session.merge(Race(race_id="200806010101", race_number=1,
                       race_date=datetime.date(2008, 6, 1), venue_code="05"))
    session.flush()
    session.merge(RaceHorse(race_id="200806010101", horse_id="H1", horse_number=1,
                            entry_status=EntryStatus.STARTED))
    session.commit()

    b = client.get("/api/v1/horses/H1/history", params={"page": 5, "page_size": 10}).json()
    assert b["total"] == 1 and b["items"] == [] and b["has_next"] is False
