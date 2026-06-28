"""T021 (US2): POST /ops/v1/days/{date}/refresh + GET /ops/v1/batches/{trace_id} contract."""

from __future__ import annotations

import pytest

from tests._synth import seed_race

pytestmark = pytest.mark.integration

DATE = "2024-12-28"
RID1 = "202406050911"
RID2 = "202406050912"


def test_refresh_day_accepts_batch(client, session):
    seed_race(session, race_id=RID1)
    seed_race(session, race_id=RID2)
    r = client.post(f"/ops/v1/days/{DATE}/refresh")
    assert r.status_code == 202
    body = r.json()
    assert body["scope"] == "day" and body["scope_value"] == DATE
    assert len(body["children"]) == 2
    assert body["poll_url"] == f"/ops/v1/batches/{body['trace_id']}"
    assert {c["scope_value"] for c in body["children"]} == {RID1, RID2}


def test_batch_status_readable(client, session):
    seed_race(session, race_id=RID1)
    trace_id = client.post(f"/ops/v1/days/{DATE}/refresh").json()["trace_id"]
    r = client.get(f"/ops/v1/batches/{trace_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == trace_id and body["scope_value"] == DATE
    assert body["total"] == 1 and len(body["children"]) == 1


def test_empty_day_404(client):
    r = client.post("/ops/v1/days/2030-01-01/refresh")
    assert r.status_code == 404 and r.json()["code"] == "no_races_on_date"


def test_bad_date_422(client):
    r = client.post("/ops/v1/days/not-a-date/refresh")
    assert r.status_code == 422


def test_unknown_batch_404(client):
    r = client.get("/ops/v1/batches/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["code"] == "batch_not_found"
