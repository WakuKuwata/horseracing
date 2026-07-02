"""importance router (Feature 040 US2): /models/{model_version}/importance.

Read-only split-gain feature importance for a model_version, READ from the persisted
``model_versions.metrics_summary`` JSONB (written by the training harness at save time). The API
never recomputes (ML-free / read-only). Missing model -> 404 model_not_found; model present but no
importance recorded (e.g. degenerate model or pre-040 run) -> typed 404 importance_unavailable.

Gain importance is biased toward high-gain-split features, so it is labelled narrowly as
"split-gain (gain) importance" in the UI, not general "feature importance" (no overclaiming).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import model_metrics_summary
from ..schemas import ImportanceResponse, ImportanceValue

router = APIRouter()


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@router.get(
    "/models/{model_version}/importance",
    response_model=ImportanceResponse,
    tags=["importance"],
)
def importance(model_version: str, session: Session = Depends(get_session)):
    exists, summary = model_metrics_summary(session, model_version)
    if not exists:
        return _err(404, "model_not_found", f"model_version {model_version} not found")

    imp = (summary or {}).get("importance") or {}
    vals = imp.get("values") or {}
    if not vals:
        return _err(
            404, "importance_unavailable",
            f"no feature importance recorded for {model_version}",
        )
    # deterministic: gain descending, feature name ascending on ties
    ordered = sorted(vals.items(), key=lambda kv: (-float(kv[1]), kv[0]))
    return ImportanceResponse(
        model_version=model_version,
        type=str(imp.get("type", "gain")),
        values=[ImportanceValue(feature=f, gain=float(g)) for f, g in ordered],
    )
