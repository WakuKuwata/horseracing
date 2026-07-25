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

from pydantic import ValidationError

from ..deps import get_session
from ..queries import latest_diagnostic_run
from ..schemas import (
    ErrorBody,
    SegmentAccuracyPayloadV1,
    SegmentAccuracyResponse,
    SegmentEdgeResponse,
    SegmentEdgeRow,
)

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


#: payload contract versions this viewer understands (codex P0#1: unknown => typed error,
#: NEVER a silently-nulled render).
_SUPPORTED_METRIC_CONTRACTS = frozenset({"sa-v1"})


@router.get("/diagnostics/segment-accuracy", response_model=SegmentAccuracyResponse,
            tags=["diagnostics"],
            responses={404: {"model": ErrorBody}, 409: {"model": ErrorBody}})
def segment_accuracy(session: Session = Depends(get_session)):
    """Feature 083: newest 082 segment-accuracy run, transcribed through the typed v1 schema.

    021 discipline: transcription only — no recompute, no reorder, no augmentation. The
    payload is validated with extra="forbid" (the 075 splat-null countermeasure): a malformed
    or unknown-version persisted run fails CLOSED as a typed 409, never renders as nulls, and
    never falls back to an older run."""
    run = latest_diagnostic_run(session, "segment_accuracy")
    if run is None:
        return _err(
            404, "diagnostic_unavailable",
            "no persisted segment_accuracy run — run `training accuracy-readout --persist`",
        )
    raw = run.payload or {}
    version = (raw.get("instrument_contract") or {}).get("metric_contract_version")
    if version not in _SUPPORTED_METRIC_CONTRACTS:
        return _err(
            409, "diagnostic_contract_unsupported",
            f"persisted metric_contract_version {version!r} is not supported by this viewer "
            f"(supported: {sorted(_SUPPORTED_METRIC_CONTRACTS)})",
        )
    try:
        payload = SegmentAccuracyPayloadV1.model_validate(raw)
    except ValidationError as exc:
        return _err(
            409, "diagnostic_contract_unsupported",
            f"persisted segment_accuracy payload failed the v1 contract: {exc.errors()[:3]}",
        )
    return SegmentAccuracyResponse(
        diagnostic_run_id=str(run.diagnostic_run_id),
        computed_at=run.computed_at,
        date_from=run.date_from,
        date_to=run.date_to,
        logic_version=run.logic_version,
        payload=payload,
    )
