"""Feature 054: diagnostics_store — verbatim payload transcription + append-only persistence."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import DiagnosticRun
from sqlalchemy import select

from horseracing_eval.diagnostics_store import (
    KIND_SEGMENT_EDGE,
    save_segment_edge_run,
    segment_edge_payload,
)
from horseracing_eval.segment_edge import SegmentEdgeReport, SegmentRow

pytestmark = pytest.mark.integration

_ROW = SegmentRow(axis="surface", segment="芝", n=100, win_rate=0.08,
                  logloss_p=0.234, logloss_q=0.202, gap=0.032, mean_p=0.08, mean_q=0.085)
_REPORT = SegmentEdgeReport(n_horses=100, rows=[_ROW])


def test_payload_is_verbatim_transcription():
    p = segment_edge_payload(_REPORT)
    assert p["n_horses"] == 100 and "SECONDARY" in p["note"]
    assert p["rows"][0] == {"axis": "surface", "segment": "芝", "n": 100, "win_rate": 0.08,
                            "logloss_p": 0.234, "logloss_q": 0.202, "gap": 0.032,
                            "mean_p": 0.08, "mean_q": 0.085}


def test_save_appends_never_overwrites(session):
    for _ in range(2):
        save_segment_edge_run(session, _REPORT,
                              date_from=datetime.date(2024, 1, 1), date_to=None,
                              logic_version="diag=segment_edge;test")
        session.commit()
    rows = session.scalars(
        select(DiagnosticRun).where(DiagnosticRun.kind == KIND_SEGMENT_EDGE)).all()
    assert len(rows) == 2  # append-only
    assert all(r.payload["rows"][0]["gap"] == 0.032 for r in rows)
    assert rows[0].logic_version == "diag=segment_edge;test"
