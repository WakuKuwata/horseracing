"""T021 (US2, A): POST /ops/v1/days/{date}/refresh + GET /ops/v1/batches/{trace_id} contract.

Day refresh now returns 202 immediately with NO children — the worker discovers the day's races
from netkeiba and fans out children later; the front polls the batch for progress."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

DATE = "2024-12-28"


def test_refresh_day_accepts_batch(client):
    # any date is accepted (discovery, not the DB, decides the race set) — children come later.
    r = client.post(f"/ops/v1/days/{DATE}/refresh")
    assert r.status_code == 202
    body = r.json()
    assert body["scope"] == "day" and body["scope_value"] == DATE
    assert body["children"] == []                      # not discovered yet (worker fans out)
    assert body["status"] == "queued"
    assert body["poll_url"] == f"/ops/v1/batches/{body['trace_id']}"


def test_batch_poll_readable_before_discovery(client):
    # polling right after POST must NOT 404 — the parent is recognised with 0 children.
    trace_id = client.post(f"/ops/v1/days/{DATE}/refresh").json()["trace_id"]
    r = client.get(f"/ops/v1/batches/{trace_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == trace_id and body["scope_value"] == DATE
    assert body["total"] == 0 and body["children"] == [] and body["status"] == "queued"


def test_unknown_day_still_accepted(client):
    # a day with no DB races is no longer a 404 — it's accepted; the worker discovers (or finds 0).
    r = client.post("/ops/v1/days/2030-01-01/refresh")
    assert r.status_code == 202 and r.json()["scope_value"] == "2030-01-01"


def test_bad_date_422(client):
    r = client.post("/ops/v1/days/not-a-date/refresh")
    assert r.status_code == 422


def test_unknown_batch_404(client):
    r = client.get("/ops/v1/batches/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["code"] == "batch_not_found"
