"""predictions router (Feature 014): /races/{race_id}/predictions.

Deterministic run selection + per-horse win/top2/top3 + audit. Joint probabilities ONLY when a
bet_type is given (never a full grid unprompted): computed on the canonical population, serialized
with db.canonical_selection (parity with 011/012), ordered by (-prob, selection), capped to top-K.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from horseracing_db.enums import BetType
from horseracing_db.selection import canonical_selection
from horseracing_probability.engine import joint_probabilities
from sqlalchemy.orm import Session

from ..deps import get_session
from ..queries import get_race, run_predictions
from ..schemas import HorsePrediction, JointEntry, PredictionResponse, RunAudit
from ..selection import canonical_win_probs, select_prediction_run

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")
_EXOTIC = set(BetType.EXOTIC)


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _joint_map(jp, bet_type: str):
    return {
        BetType.PLACE: jp.place, BetType.QUINELLA: jp.quinella, BetType.EXACTA: jp.exacta,
        BetType.WIDE: jp.wide, BetType.TRIO: jp.trio, BetType.TRIFECTA: jp.trifecta,
    }.get(bet_type)


def _numbers(key) -> list[int]:
    return [int(key)] if isinstance(key, int) else [int(x) for x in key]


@router.get("/races/{race_id}/predictions", response_model=PredictionResponse, tags=["predictions"])
def predictions(
    race_id: str,
    bet_type: str | None = Query(default=None),
    top: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if get_race(session, race_id) is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    if bet_type is not None and bet_type not in _EXOTIC:
        return _err(422, "invalid_bet_type", f"bet_type must be one of {sorted(_EXOTIC)}")

    run = select_prediction_run(session, race_id)
    if run is None:
        return PredictionResponse(race_id=race_id, run=None, horses=[])  # typed-empty

    horses = [
        HorsePrediction(
            horse_number=n, horse_id=hid,
            win=(float(w) if w is not None else None),
            top2=(float(t2) if t2 is not None else None),
            top3=(float(t3) if t3 is not None else None),
        )
        for (n, hid, _status, w, t2, t3) in run_predictions(
            session, run_id=run.prediction_run_id, race_id=race_id
        )
    ]
    audit = RunAudit(
        prediction_run_id=str(run.prediction_run_id), model_version=run.model_version,
        logic_version=run.logic_version, computed_at=run.computed_at,
    )
    resp = PredictionResponse(race_id=race_id, run=audit, horses=horses)

    if bet_type is None:
        return resp  # no joint without an explicit bet_type (grid protection)

    canon = canonical_win_probs(session, run_id=run.prediction_run_id, race_id=race_id)
    if len(canon) < 2:
        return _err(409, "no_usable_probabilities", "no usable win probabilities for joint")
    jp = joint_probabilities(canon, field_size=len(canon))  # may raise -> 409 via app handler
    jmap = _joint_map(jp, bet_type)
    if not jmap:  # field rule (place/wide for tiny fields) or N<3
        resp.joint = []
        resp.joint_bet_type = bet_type
        return resp
    entries = [
        JointEntry(selection=canonical_selection(bet_type, _numbers(key)), prob=float(p))
        for key, p in jmap.items() if p is not None
    ]
    entries.sort(key=lambda e: (-e.prob, e.selection))
    resp.joint = entries[:top]
    resp.joint_bet_type = bet_type
    resp.joint_logic_version = "joint=PL/Harville(009);canonical_started"
    return resp
