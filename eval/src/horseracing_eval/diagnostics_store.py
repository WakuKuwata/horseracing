"""Feature 054: persist offline diagnostics to diagnostic_runs for read-only display.

The heavy walk-forward diagnostics (047 segment-edge) run via the training CLI; this module
serialises their report VERBATIM into an append-only diagnostic_runs row (021 discipline: the
API/admin only transcribe, never recompute; constitution III: no derived metrics added here).
"""

from __future__ import annotations

import dataclasses
import datetime

from horseracing_db.models import DiagnosticRun
from sqlalchemy.orm import Session

from .segment_edge import SegmentEdgeReport

KIND_SEGMENT_EDGE = "segment_edge"


def segment_edge_payload(report: SegmentEdgeReport) -> dict:
    """SegmentEdgeReport → JSONB payload (verbatim transcription of the 047 output)."""
    return {
        "n_horses": report.n_horses,
        "note": report.note,
        "rows": [dataclasses.asdict(r) for r in report.rows],
    }


def save_segment_edge_run(
    session: Session,
    report: SegmentEdgeReport,
    *,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    logic_version: str,
) -> DiagnosticRun:
    """Append one segment_edge diagnostic run (never overwrites; caller commits)."""
    run = DiagnosticRun(
        kind=KIND_SEGMENT_EDGE,
        date_from=date_from,
        date_to=date_to,
        logic_version=logic_version,
        payload=segment_edge_payload(report),
    )
    session.add(run)
    session.flush()
    return run


KIND_SEGMENT_ACCURACY = "segment_accuracy"


def save_segment_accuracy_run(
    session: Session,
    payload: dict,
    *,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    logic_version: str,
) -> DiagnosticRun:
    """Feature 082: append one segment_accuracy run (payload VERBATIM — 054 discipline: this
    layer transcribes, never recomputes or augments; never overwrites; caller commits).
    Kind is deliberately separate from ``segment_edge`` (different estimand, codex)."""
    run = DiagnosticRun(
        kind=KIND_SEGMENT_ACCURACY,
        date_from=date_from,
        date_to=date_to,
        logic_version=logic_version,
        payload=payload,
    )
    session.add(run)
    session.flush()
    return run
