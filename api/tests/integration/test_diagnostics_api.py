"""Feature 054: GET /diagnostics/segment-edge — latest-run transcription + typed 404."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import DiagnosticRun

pytestmark = pytest.mark.integration

_ROW = {"axis": "q_band", "segment": "q>=0.30(本命)", "n": 1000, "win_rate": 0.41,
        "logloss_p": 0.65, "logloss_q": 0.42, "gap": 0.23, "mean_p": 0.185, "mean_q": 0.405}


def _persist(session, *, computed_at, n_horses):
    session.add(DiagnosticRun(
        kind="segment_edge", logic_version="diag=segment_edge;test",
        date_from=datetime.date(2021, 1, 1), date_to=datetime.date(2025, 10, 26),
        payload={"n_horses": n_horses, "note": "SECONDARY diagnostic (047).", "rows": [_ROW]},
        computed_at=computed_at,
    ))
    session.commit()


def test_returns_latest_run_transcribed(client, session):
    _persist(session, computed_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC), n_horses=1)
    _persist(session, computed_at=datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC), n_horses=2)
    body = client.get("/api/v1/diagnostics/segment-edge").json()
    assert body["n_horses"] == 2                        # newest run wins
    assert body["date_from"] == "2021-01-01"
    assert "SECONDARY" in body["note"]
    assert body["rows"][0]["gap"] == 0.23 and body["rows"][0]["segment"] == "q>=0.30(本命)"


def test_typed_404_when_nothing_persisted(client, session):
    r = client.get("/api/v1/diagnostics/segment-edge")
    assert r.status_code == 404 and r.json()["code"] == "diagnostic_unavailable"
