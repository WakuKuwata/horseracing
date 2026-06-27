"""T007 (018): /health verifies DB connectivity + alembic schema-at-head, read-only (SC-005)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_health_ok_when_schema_at_head(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    # backward-compatible fields (014) preserved
    assert body["status"] == "ok" and body["api_version"] == "v1" and "schema_version" in body
    # 018 additions: schema-at-head verification
    assert body["db"] is True
    assert body["schema_in_sync"] is True
    assert body["alembic_current"] == body["alembic_head"] is not None


def test_health_503_when_schema_not_at_head(client, session):
    head = client.get("/api/v1/health").json()["alembic_head"]
    # simulate an un-migrated / drifted DB by pointing alembic_version off head
    session.execute(text("UPDATE alembic_version SET version_num = 'deadbeef_not_head'"))
    session.commit()
    try:
        r = client.get("/api/v1/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert body["schema_in_sync"] is False
        assert body["alembic_current"] == "deadbeef_not_head"
        assert body["alembic_head"] == head
    finally:
        # restore head so the shared alembic_version row is not left drifted (not truncated)
        session.execute(text("UPDATE alembic_version SET version_num = :h"), {"h": head})
        session.commit()
