"""diagnostics router (Feature 054 admin console): GET /diagnostics/segment-edge.

Read-only transcription of the NEWEST persisted diagnostic_runs row (kind=segment_edge) —
computed OFFLINE by `training segment-diagnostic --persist` (fold-retraining walk-forward, 047).
The API never recomputes (021 discipline; it is ML-free and could not anyway). Nothing persisted
yet → typed 404 diagnostic_unavailable (never a silent empty).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import latest_diagnostic_run
from ..schemas import SegmentEdgeResponse, SegmentEdgeRow

router = APIRouter()


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@router.get("/diagnostics/segment-edge", response_model=SegmentEdgeResponse,
            tags=["diagnostics"])
def segment_edge(session: Session = Depends(get_session)):
    run = latest_diagnostic_run(session, "segment_edge")
    if run is None:
        return _err(
            404, "diagnostic_unavailable",
            "no persisted segment_edge run — run `training segment-diagnostic --persist`",
        )
    payload = run.payload or {}
    return SegmentEdgeResponse(
        computed_at=run.computed_at,
        date_from=run.date_from,
        date_to=run.date_to,
        logic_version=run.logic_version,
        n_horses=int(payload.get("n_horses", 0)),
        note=str(payload.get("note", "")),
        rows=[SegmentEdgeRow(**r) for r in payload.get("rows", [])],
    )
