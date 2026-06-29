"""T005 (US1): GET /horses/{id} + /horses/{id}/history contract (200 / 404 / pagination)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import Horse

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration


def test_horse_profile_200(client, session):
    # H1 runs two races (1着, 3着); give it pedigree names to display.
    seed_model(session)
    seed_race(session, race_id="200806010101", race_number=1,
              race_date=datetime.date(2008, 6, 1),
              horses={1: {"odds": 2.0, "finish": 1}})
    seed_race(session, race_id="200806020101", race_number=1,
              race_date=datetime.date(2008, 6, 8),
              horses={1: {"odds": 3.0, "finish": 3}})
    h = session.get(Horse, "H1")
    h.sire_name, h.dam_name, h.damsire_name = "ParentSire", "ParentDam", "ParentDamsire"
    session.commit()

    r = client.get("/api/v1/horses/H1")
    assert r.status_code == 200
    b = r.json()
    assert b["horse_id"] == "H1"
    assert b["sire_name"] == "ParentSire" and b["damsire_name"] == "ParentDamsire"
    assert b["starts"] == 2 and b["wins"] == 1
    assert b["win_rate"] == 0.5


def test_horse_404(client):
    r = client.get("/api/v1/horses/NOPE")
    assert r.status_code == 404 and r.json()["code"] == "horse_not_found"


def test_horse_history_paged(client, session):
    seed_model(session)
    for i in range(1, 6):  # 5 races for H1
        seed_race(session, race_id=f"2008060{i}0101", race_number=1,
                  race_date=datetime.date(2008, 6, i), horses={1: {"odds": 2.0, "finish": i}})

    r = client.get("/api/v1/horses/H1/history", params={"page": 1, "page_size": 2})
    assert r.status_code == 200
    b = r.json()
    assert b["total"] == 5 and b["has_next"] is True and len(b["items"]) == 2
    # newest first (race_date DESC) -> the 2008-06-05 race leads
    assert b["items"][0]["race_date"] == "2008-06-05"


def test_horse_history_404(client):
    assert client.get("/api/v1/horses/NOPE/history").status_code == 404
