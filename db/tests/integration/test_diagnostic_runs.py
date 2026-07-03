"""Feature 054: diagnostic_runs — append-only persistence + latest-per-kind read order."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from horseracing_db.models import DiagnosticRun

pytestmark = pytest.mark.integration


def test_insert_and_latest_per_kind(session):
    a = DiagnosticRun(kind="segment_edge", logic_version="seg-047;v1",
                      date_from=datetime.date(2021, 1, 1), date_to=datetime.date(2025, 10, 26),
                      payload={"n_horses": 10, "rows": []},
                      computed_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC))
    b = DiagnosticRun(kind="segment_edge", logic_version="seg-047;v1",
                      payload={"n_horses": 20, "rows": []},
                      computed_at=datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC))
    other = DiagnosticRun(kind="other_kind", logic_version="x",
                          payload={},
                          computed_at=datetime.datetime(2026, 7, 4, tzinfo=datetime.UTC))
    session.add_all([a, b, other])
    session.commit()

    latest = session.scalars(
        select(DiagnosticRun)
        .where(DiagnosticRun.kind == "segment_edge")
        .order_by(DiagnosticRun.computed_at.desc(), DiagnosticRun.diagnostic_run_id)
    ).first()
    assert latest is not None and latest.payload["n_horses"] == 20  # newest wins
    # append-only: both segment_edge rows remain
    n = len(session.scalars(
        select(DiagnosticRun).where(DiagnosticRun.kind == "segment_edge")).all())
    assert n == 2
