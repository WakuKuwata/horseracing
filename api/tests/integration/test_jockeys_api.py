"""T014 (US2): GET /jockeys/{id} + /jockeys/{id}/history contract (200 / 404 / pagination)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, RaceResult

pytestmark = pytest.mark.integration

JID = "J1"


def _mount(session, *, race_id, day, horse_id, finish_order):
    session.merge(Race(race_id=race_id, race_number=1, race_date=datetime.date(2008, 6, day),
                       venue_code="05"))
    session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
    session.flush()
    session.merge(RaceHorse(race_id=race_id, horse_id=horse_id, horse_number=1, jockey_id=JID,
                            entry_status=EntryStatus.STARTED))
    session.merge(RaceResult(race_id=race_id, horse_id=horse_id, finish_order=finish_order,
                             result_status=ResultStatus.FINISHED))


def test_jockey_profile_200(client, session):
    session.merge(Jockey(jockey_id=JID, jockey_name="テスト騎手"))
    session.flush()
    _mount(session, race_id="200806010101", day=1, horse_id="HA", finish_order=1)
    _mount(session, race_id="200806020101", day=2, horse_id="HB", finish_order=4)
    session.commit()

    b = client.get(f"/api/v1/jockeys/{JID}").json()
    assert b["jockey_id"] == JID and b["jockey_name"] == "テスト騎手"
    assert b["mounts"] == 2 and b["wins"] == 1 and b["win_rate"] == 0.5


def test_jockey_404(client):
    r = client.get("/api/v1/jockeys/NOPE")
    assert r.status_code == 404 and r.json()["code"] == "jockey_not_found"


def test_jockey_history_paged(client, session):
    session.merge(Jockey(jockey_id=JID, jockey_name="テスト騎手"))
    session.flush()
    for i in range(1, 4):
        _mount(session, race_id=f"2008060{i}0101", day=i, horse_id=f"H{i}", finish_order=i)
    session.commit()

    b = client.get(f"/api/v1/jockeys/{JID}/history", params={"page_size": 2}).json()
    assert b["total"] == 3 and b["has_next"] is True and len(b["items"]) == 2
    assert b["items"][0]["race_date"] == "2008-06-03"  # newest first
    assert b["items"][0]["horse_name"] == "H3"


def test_jockey_history_404(client):
    assert client.get("/api/v1/jockeys/NOPE/history").status_code == 404
