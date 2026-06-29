"""T015 (US2): jockey aggregate母数規則 — mounts=started, placings=finished, avg=finished."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, RaceResult

pytestmark = pytest.mark.integration

JID = "JX"


def _mount(session, *, race_id, day, horse_id, entry_status, result_status=None, finish_order=None):
    session.merge(Race(race_id=race_id, race_number=1, race_date=datetime.date(2008, 6, day),
                       venue_code="05"))
    session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
    session.flush()
    session.merge(RaceHorse(race_id=race_id, horse_id=horse_id, horse_number=1, jockey_id=JID,
                            entry_status=entry_status))
    if result_status is not None:
        session.merge(RaceResult(race_id=race_id, horse_id=horse_id, finish_order=finish_order,
                                 result_status=result_status))


def test_jockey_stats_rules(client, session):
    session.merge(Jockey(jockey_id=JID, jockey_name="JX"))
    session.flush()
    # 3 mounts: 1着, 2着, 中止(stopped); +1 取消 (not a mount)
    _mount(session, race_id="200806010101", day=1, horse_id="HA",
           entry_status=EntryStatus.STARTED, result_status=ResultStatus.FINISHED, finish_order=1)
    _mount(session, race_id="200806020101", day=2, horse_id="HB",
           entry_status=EntryStatus.STARTED, result_status=ResultStatus.FINISHED, finish_order=2)
    _mount(session, race_id="200806030101", day=3, horse_id="HC",
           entry_status=EntryStatus.STARTED, result_status=ResultStatus.STOPPED, finish_order=None)
    _mount(session, race_id="200806040101", day=4, horse_id="HD",
           entry_status=EntryStatus.CANCELLED)
    session.commit()

    b = client.get(f"/api/v1/jockeys/{JID}").json()
    assert b["mounts"] == 3                 # cancelled excluded
    assert b["wins"] == 1 and b["seconds_in"] == 2 and b["shows_in"] == 2
    assert b["win_rate"] == 1 / 3
    assert abs(b["avg_finish"] - 1.5) < 1e-9  # (1+2)/2, stopped excluded


def test_jockey_zero_mounts_null_rates(client, session):
    session.merge(Jockey(jockey_id="JNEW", jockey_name="New"))
    session.commit()
    b = client.get("/api/v1/jockeys/JNEW").json()
    assert b["mounts"] == 0 and b["win_rate"] is None and b["avg_finish"] is None
