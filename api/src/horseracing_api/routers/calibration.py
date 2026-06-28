"""calibration router (Feature 021 US2): /models/{model_version}/calibration.

Read-only walk-forward OOS reliability for a model_version, READ from the persisted
``model_versions.metrics_summary`` JSONB (computed offline by the eval harness at adoption). The
API never recomputes (would be in-sample-optimistic and slow). Missing model -> 404; model present
but no reliability recorded -> typed 404 ``calibration_unavailable`` (never a silent empty, R8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import model_metrics_summary
from ..schemas import CalibrationBin, CalibrationResponse

router = APIRouter()

_LABELS = {"win", "top2", "top3"}


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@router.get(
    "/models/{model_version}/calibration",
    response_model=CalibrationResponse,
    tags=["calibration"],
)
def calibration(
    model_version: str,
    label: str = Query(default="win"),
    session: Session = Depends(get_session),
):
    if label not in _LABELS:
        return _err(422, "invalid_label", f"label must be one of {sorted(_LABELS)}")

    exists, summary = model_metrics_summary(session, model_version)
    if not exists:
        return _err(404, "model_not_found", f"model_version {model_version} not found")

    ev = (summary or {}).get("eval") or {}
    rel = (ev.get("reliability") or {}).get(label)
    if not rel or not rel.get("bins"):
        return _err(
            404, "calibration_unavailable",
            f"no walk-forward OOS reliability recorded for {model_version} ({label})",
        )

    ece = ((ev.get("overall") or {}).get(label) or {}).get("ece")
    bins = [
        CalibrationBin(
            pred_lo=b["pred_lo"], pred_hi=b["pred_hi"], pred_mean=b.get("pred_mean"),
            realized_rate=b.get("realized_rate"),
            realized_ci_low=b.get("realized_ci_low"), realized_ci_high=b.get("realized_ci_high"),
            count=int(b.get("count", 0)), suppressed=bool(b.get("suppressed", False)),
        )
        for b in rel["bins"]
    ]
    return CalibrationResponse(
        model_version=model_version,
        label=label,
        valid_years=[int(y) for y in ev.get("valid_years", [])],
        n_total=int(rel.get("n_total", 0)),
        ece=(float(ece) if ece is not None else None),
        bins=bins,
    )
