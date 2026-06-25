"""T011 (US1): /health, /races (filter/page/stable order), /races/{id}, 404/422 (SC-001/SC-006)."""

from __future__ import annotations

import datetime

import pytest

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_HORSES = {1: {"win": 0.5, "odds": 2.0}, 2: {"win": 0.5, "odds": 3.0}}


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["api_version"] == "v1" and "schema_version" in body


def test_races_filter_and_pagination(client, session):
    seed_model(session)
    for i in range(1, 6):
        seed_race(session, race_id=f"2008060101{i:02d}", race_number=i,
                  race_date=datetime.date(2008, 6, 1), venue_code="05", horses=_HORSES)
    seed_race(session, race_id="200806020101", race_date=datetime.date(2008, 6, 2),
              venue_code="06", horses=_HORSES)

    r = client.get("/api/v1/races", params={"date": "2008-06-01", "venue": "05",
                                            "page": 1, "page_size": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5 and body["has_next"] is True and len(body["items"]) == 3
    # stable order: all from the filtered date/venue
    assert all(it["venue_code"] == "05" for it in body["items"])
    ids = [it["race_id"] for it in body["items"]]
    assert ids == sorted(ids)  # same date -> race_number/race_id ascending, stable


def test_page_size_max_enforced(client):
    r = client.get("/api/v1/races", params={"page_size": 5000})
    assert r.status_code == 422  # exceeds max page_size


def test_race_detail_and_404_422(client, session):
    seed_model(session)
    seed_race(session, race_id="200806010101", horses=_HORSES)
    r = client.get("/api/v1/races/200806010101")
    assert r.status_code == 200
    body = r.json()
    assert body["race_id"] == "200806010101" and len(body["horses"]) == 2
    assert {h["horse_number"] for h in body["horses"]} == {1, 2}

    assert client.get("/api/v1/races/200806019999").status_code == 404   # not found
    assert client.get("/api/v1/races/bad-id").status_code == 422          # bad format
