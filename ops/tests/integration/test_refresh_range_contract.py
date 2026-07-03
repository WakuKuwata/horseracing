"""Feature 053: refresh-range endpoint contract (202/422) + dedup + audit row."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob
from sqlalchemy import select

pytestmark = pytest.mark.integration

_BODY = {"date_from": "2025-01-05", "date_to": "2025-01-06"}


def test_refresh_range_accepts_202_with_audit(client, session):
    r = client.post("/ops/v1/refresh-range", json=_BODY)
    assert r.status_code == 202
    body = r.json()
    assert body["scope"] == "range" and body["scope_value"] == "2025-01-05..2025-01-06"
    assert body["reused"] is False
    assert f"/ops/v1/jobs/{body['job_id']}" == body["poll_url"]
    job = session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "refresh_range")
    ).first()
    assert job is not None and job.scope == "range"
    assert job.summary["kind"] == "refresh_range" and job.summary["source"] == "manual"


def test_refresh_range_dedups_active_job(client):
    first = client.post("/ops/v1/refresh-range", json=_BODY).json()
    second = client.post("/ops/v1/refresh-range", json=_BODY).json()
    assert second["reused"] is True and second["job_id"] == first["job_id"]


def test_refresh_range_422_inverted(client):
    r = client.post("/ops/v1/refresh-range",
                    json={"date_from": "2025-01-06", "date_to": "2025-01-05"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_range"


def test_refresh_range_422_too_wide(client):
    r = client.post("/ops/v1/refresh-range",
                    json={"date_from": "2025-01-01", "date_to": "2025-03-01"})
    assert r.status_code == 422 and r.json()["code"] == "range_too_wide"


def test_refresh_range_422_bad_date(client):
    r = client.post("/ops/v1/refresh-range",
                    json={"date_from": "not-a-date", "date_to": "2025-01-05"})
    assert r.status_code == 422
