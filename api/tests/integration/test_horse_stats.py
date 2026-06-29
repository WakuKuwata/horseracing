"""T006 (US1): career aggregate母数規則 — started=denominator, finished=placings, avg=finished."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

pytestmark = pytest.mark.integration

HID = "HX"


def _entry(session, *, race_id, day, entry_status, result_status=None, finish_order=None):
    session.merge(Race(race_id=race_id, race_number=1, race_date=datetime.date(2008, 6, day),
                       venue_code="05"))
    session.flush()
    session.merge(RaceHorse(race_id=race_id, horse_id=HID, horse_number=1,
                            entry_status=entry_status))
    if result_status is not None:
        session.merge(RaceResult(race_id=race_id, horse_id=HID, finish_order=finish_order,
                                 result_status=result_status))


def test_career_stats_follow_denominator_rules(client, session):
    session.merge(Horse(horse_id=HID, horse_name="HX"))
    session.flush()
    # 4 starts: 1着, 2着, 5着, 中止(stopped, no finish_order); +1 取消(cancelled, not a start)
    _entry(session, race_id="200806010101", day=1, entry_status=EntryStatus.STARTED,
           result_status=ResultStatus.FINISHED, finish_order=1)
    _entry(session, race_id="200806020101", day=2, entry_status=EntryStatus.STARTED,
           result_status=ResultStatus.FINISHED, finish_order=2)
    _entry(session, race_id="200806030101", day=3, entry_status=EntryStatus.STARTED,
           result_status=ResultStatus.FINISHED, finish_order=5)
    _entry(session, race_id="200806040101", day=4, entry_status=EntryStatus.STARTED,
           result_status=ResultStatus.STOPPED, finish_order=None)
    _entry(session, race_id="200806050101", day=5, entry_status=EntryStatus.CANCELLED)
    session.commit()

    b = client.get("/api/v1/horses/HX").json()
    assert b["starts"] == 4                    # cancelled excluded
    assert b["wins"] == 1
    assert b["seconds_in"] == 2                 # 1着 + 2着
    assert b["shows_in"] == 2                   # 5着 is not within 3
    assert b["win_rate"] == 0.25               # 1/4
    assert b["quinella_rate"] == 0.5           # 2/4
    assert b["show_rate"] == 0.5               # 2/4
    assert abs(b["avg_finish"] - (1 + 2 + 5) / 3) < 1e-9  # stopped (no finish) excluded


def test_zero_starts_rates_are_null(client, session):
    session.merge(Horse(horse_id="DEBUT", horse_name="Debut"))
    session.commit()
    b = client.get("/api/v1/horses/DEBUT").json()
    assert b["starts"] == 0 and b["wins"] == 0
    assert b["win_rate"] is None and b["avg_finish"] is None  # Unknown != 0
