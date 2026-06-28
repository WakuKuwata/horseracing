"""T011 (US1): POST /ops/v1/races/{id}/refresh + GET /ops/v1/jobs/{id} contract (202/404/422)."""

from __future__ import annotations

import pytest

from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_refresh_race_accepts_202(client, session):
    seed_race(session, race_id=RID)
    r = client.post(f"/ops/v1/races/{RID}/refresh")
    assert r.status_code == 202
    body = r.json()
    assert body["scope"] == "race" and body["scope_value"] == RID
    assert body["status"] == "queued" and body["reused"] is False
    assert body["poll_url"] == f"/ops/v1/jobs/{body['job_id']}"


def test_job_status_readable(client, session):
    seed_race(session, race_id=RID)
    job_id = client.post(f"/ops/v1/races/{RID}/refresh").json()["job_id"]
    r = client.get(f"/ops/v1/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == job_id and body["job_type"] == "refresh_race"
    assert body["status"] == "queued" and body["scope_value"] == RID


def test_unknown_race_404(client):
    r = client.post("/ops/v1/races/209900010101/refresh")
    assert r.status_code == 404 and r.json()["code"] == "race_not_found"


def test_bad_race_id_422(client):
    r = client.post("/ops/v1/races/not-a-race/refresh")
    assert r.status_code == 422 and r.json()["code"] == "invalid_race_id"


def test_unknown_job_404(client):
    r = client.get("/ops/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["code"] == "job_not_found"
