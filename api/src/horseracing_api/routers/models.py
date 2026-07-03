"""models router (Feature 051 admin console): GET /models — the model registry list.

Read-only transcription of persisted ``model_versions`` rows + their ``metrics_summary`` JSONB
(written by the training harness at save time; the API never recomputes = 021 discipline).
Missing metrics keys → null, never 0-filled and never a 500. Deterministic order: active first →
created_at DESC → model_version. Consumed by the admin SPA (NOT the end-user front).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import list_model_versions
from ..schemas import ModelListResponse, ModelVersionRow

router = APIRouter()


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _i(x) -> int | None:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _row(mv) -> ModelVersionRow:
    summary = mv.metrics_summary or {}
    ev = summary.get("eval") or {}
    win = (ev.get("overall") or {}).get("win") or {}
    tr = summary.get("training") or {}
    adoption = tr.get("adoption") or {}
    return ModelVersionRow(
        model_version=mv.model_version,
        model_family=mv.model_family,
        feature_version=mv.feature_version,
        label_schema=mv.label_schema,
        adoption_status=mv.adoption_status,
        created_at=mv.created_at,
        win_log_loss=_f(win.get("log_loss")),
        win_auc=_f(win.get("auc")),
        win_ece=_f(win.get("ece")),
        win_brier=_f(win.get("brier")),
        objective=tr.get("objective"),
        calibration=tr.get("calibration"),
        train_through=tr.get("train_through"),
        n_model_rows=_i(tr.get("n_model_rows")),
        git_sha=tr.get("git_sha"),
        adopted=adoption.get("adopted"),
        has_calibration=bool(ev.get("reliability")),
        has_importance=bool((summary.get("importance") or {}).get("values")),
    )


@router.get("/models", response_model=ModelListResponse, tags=["models"])
def models(session: Session = Depends(get_session)):
    return ModelListResponse(items=[_row(mv) for mv in list_model_versions(session)])
