"""Feature 086: typed, fail-soft projection of ``summary.capture`` on GET /jobs."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from horseracing_api import API_PREFIX
from horseracing_api.deps import get_session
from horseracing_api.routers import jobs as jobs_router

_NOW = datetime.datetime(2026, 7, 28, 12, 0, tzinfo=datetime.UTC)


def _job(*, summary):
    return SimpleNamespace(
        ingestion_job_id="00000000-0000-4000-8000-000000000086",
        source="netkeiba",
        job_type="predict",
        scope="race",
        scope_value="202607280101",
        status="succeeded",
        trace_id=None,
        retry_count=0,
        started_at=_NOW,
        completed_at=None,
        error_message=None,
        processed_rows=None,
        skipped_rows=None,
        error_count=None,
        created_at=_NOW,
        summary=summary,
    )


@pytest.fixture
def jobs_client(monkeypatch):
    rows = []
    monkeypatch.setattr(jobs_router, "list_jobs", lambda *args, **kwargs: rows)
    app = FastAPI()
    app.include_router(jobs_router.router, prefix=API_PREFIX)
    app.dependency_overrides[get_session] = lambda: MagicMock()
    with TestClient(app) as client:
        yield client, rows


def test_summary_null_is_http_200(jobs_client) -> None:
    client, rows = jobs_client
    rows.append(_job(summary=None))

    response = client.get(f"{API_PREFIX}/jobs")

    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["summary"] is None
    assert row["capture"] is None


def test_started_capture_without_reason_is_http_200_and_reason_null(jobs_client) -> None:
    client, rows = jobs_client
    summary = {
        "source": "manual",
        "capture": {
            "state": "started",
            "outcome": "unknown",
            "capture_strength": None,
            "confirmation_eligible": None,
            "seconds_to_post": None,
            "chaos_snapshot_id": None,
        },
    }
    rows.append(_job(summary=summary))

    response = client.get(f"{API_PREFIX}/jobs")

    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["summary"] == summary
    assert row["capture"]["state"] == "started"
    assert row["capture"]["outcome"] == "unknown"
    assert row["capture"]["reason"] is None


def test_completed_capture_projects_every_public_field_and_open_reason(jobs_client) -> None:
    client, rows = jobs_client
    capture = {
        "state": "done",
        "outcome": "skipped",
        "reason": "brand_new_reason",
        "capture_strength": "confirmatory",
        "confirmation_eligible": True,
        "seconds_to_post": 7_200,
        "chaos_snapshot_id": "00000000-0000-4000-8000-000000000084",
    }
    summary = {"source": "manual", "capture": capture}
    rows.append(_job(summary=summary))

    response = client.get(f"{API_PREFIX}/jobs")

    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["summary"] == summary
    assert row["capture"] == capture


@pytest.mark.parametrize(
    "capture",
    [
        {"state": "done", "outcome": "future_outcome", "reason": "raw_reason"},
        {"outcome": "captured", "reason": "ok"},
        "not-a-dict",
    ],
)
def test_malformed_capture_is_fail_soft_and_keeps_summary(
    jobs_client,
    capture,
) -> None:
    client, rows = jobs_client
    summary = {"source": "manual", "capture": capture}
    rows.append(_job(summary=summary))

    response = client.get(f"{API_PREFIX}/jobs")

    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["summary"] == summary
    assert row["capture"] is None


def test_openapi_exposes_typed_capture_with_open_optional_reason() -> None:
    from horseracing_api.app import app

    schemas = app.openapi()["components"]["schemas"]
    capture = schemas["JobCaptureRow"]
    properties = capture["properties"]
    assert properties["state"]["enum"] == ["started", "launched", "done"]
    assert properties["outcome"]["enum"] == [
        "captured",
        "skipped",
        "rejected",
        "failed",
        "unknown",
    ]
    assert "state" in capture["required"]
    assert "outcome" in capture["required"]
    assert "reason" not in capture.get("required", [])
    assert "enum" not in properties["reason"]
    assert properties["reason"]["anyOf"] == [{"type": "string"}, {"type": "null"}]

    job_properties = schemas["JobRow"]["properties"]
    assert set(
        (
            "ingestion_job_id",
            "source",
            "job_type",
            "scope",
            "scope_value",
            "status",
            "trace_id",
            "retry_count",
            "started_at",
            "completed_at",
            "error_message",
            "processed_rows",
            "skipped_rows",
            "error_count",
            "created_at",
        )
    ) <= set(job_properties)
    assert "summary" in job_properties
    assert "capture" in job_properties
