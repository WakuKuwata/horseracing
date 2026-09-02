"""Append-only purchase recording endpoint (Feature 106, US1)."""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from horseracing_db.models import (
    PURCHASE_KINDS,
    PredictionRun,
    PurchaseRecord,
    Race,
    RaceResult,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import Response as StarletteResponse

from ..deps import get_session

PurchaseKind = Literal[
    "as_presented",
    "modified",
    "skipped_presented",
    "no_recommendation",
    "presentation_unavailable",
    "freeform",
    "correction",
    "void",
]

_BET_TYPES = frozenset(
    {"win", "place", "quinella", "exacta", "wide", "trio", "trifecta"}
)
_SELECTION_LENGTHS = {
    "win": 1,
    "place": 1,
    "quinella": 2,
    "exacta": 2,
    "wide": 2,
    "trio": 3,
    "trifecta": 3,
}
_UNORDERED_BET_TYPES = frozenset({"quinella", "wide", "trio"})
_PRESENTED_KINDS = frozenset(
    {"as_presented", "modified", "skipped_presented", "no_recommendation"}
)
_NO_SNAPSHOT_KINDS = frozenset({"freeform", "presentation_unavailable"})
_CORRECTION_KINDS = frozenset({"correction", "void"})
_EMPTY_BET_KINDS = frozenset({"skipped_presented", "no_recommendation", "void"})


class BetIn(BaseModel):
    bet_type: str
    selection: list[int]
    amount_yen: int
    odds_used: float | None = None


class PurchaseRecordIn(BaseModel):
    race_id: str
    kind: PurchaseKind
    bets: list[BetIn]
    client_request_id: str
    prediction_run_id: uuid.UUID | None = None
    presented_snapshot: dict | None = None
    corrects_record_id: uuid.UUID | None = None
    note: str | None = None


class PurchaseRecordOut(BaseModel):
    purchase_record_id: uuid.UUID
    race_id: str
    kind: PurchaseKind
    bets: list[BetIn]
    result_pending_at_record: bool
    pending_basis_at: datetime.datetime
    recorded_at: datetime.datetime
    replayed: bool


class PurchaseErrorDetail(BaseModel):
    code: str
    message: str


class PurchaseError(BaseModel):
    detail: PurchaseErrorDetail


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": code, "message": message}},
    )


class _TypedValidationRoute(APIRoute):
    """Keep request-model failures in this endpoint's typed error envelope."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[StarletteResponse]]:
        original_handler = super().get_route_handler()

        async def typed_handler(request: Request) -> StarletteResponse:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                locations = [error.get("loc", ()) for error in exc.errors()]
                if any("kind" in location for location in locations):
                    return _error(
                        422,
                        "invalid_kind",
                        f"kind must be one of: {', '.join(PURCHASE_KINDS)}",
                    )
                if any("bets" in location for location in locations):
                    return _error(422, "invalid_bet", "bets contain an invalid field value")
                return _error(422, "invalid_request", "request body is invalid")

        return typed_handler


router = APIRouter(tags=["purchase"])
router.route_class = _TypedValidationRoute


def _payload_hash(body: object) -> str:
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _invalid_bet_message(bets: list[BetIn]) -> str | None:
    for index, bet in enumerate(bets):
        if bet.bet_type not in _BET_TYPES:
            return f"bets[{index}].bet_type is unsupported"
        if bet.amount_yen <= 0 or bet.amount_yen % 100 != 0:
            return f"bets[{index}].amount_yen must be a positive multiple of 100"

        expected_length = _SELECTION_LENGTHS[bet.bet_type]
        if len(bet.selection) != expected_length:
            return (
                f"bets[{index}].selection must contain {expected_length} horse number(s)"
            )
        if any(number <= 0 for number in bet.selection):
            return f"bets[{index}].selection must contain positive horse numbers"
        if len(set(bet.selection)) != len(bet.selection):
            return f"bets[{index}].selection must not contain duplicates"
        if (
            bet.bet_type in _UNORDERED_BET_TYPES
            and bet.selection != sorted(bet.selection)
        ):
            return f"bets[{index}].selection must be in ascending canonical order"
    return None


def _stored_bets(bets: list[BetIn]) -> list[dict[str, object]]:
    return [
        {
            "bet_type": bet.bet_type,
            "selection": list(bet.selection),
            "amount_yen": bet.amount_yen,
            "odds_used": bet.odds_used,
        }
        for bet in bets
    ]


def _response(record: PurchaseRecord, *, replayed: bool) -> PurchaseRecordOut:
    bets = [
        BetIn(
            bet_type=bet["bet_type"],
            selection=bet["selection"],
            amount_yen=bet["amount_yen"],
            odds_used=bet.get("odds_used"),
        )
        for bet in record.bets
    ]
    return PurchaseRecordOut(
        purchase_record_id=record.purchase_record_id,
        race_id=record.race_id,
        kind=record.kind,
        bets=bets,
        result_pending_at_record=record.result_pending_at_record,
        pending_basis_at=record.pending_basis_at,
        recorded_at=record.recorded_at,
        replayed=replayed,
    )


def _has_effective_record(session: Session, race_id: str) -> bool:
    """Replay the race's chain with the SAME semantics as the api-side fold.

    A correction moves the effective id to the correction row, so a later void targets the
    CORRECTION, not the base — a voided-record-id set over base rows misses that chain and
    permanently locks the race out of re-recording (found live: base → correction → void).
    """
    records = sorted(
        session.scalars(
            select(PurchaseRecord).where(PurchaseRecord.race_id == race_id)
        ).all(),
        key=lambda r: (r.recorded_at, str(r.purchase_record_id)),
    )
    current: object | None = None
    for record in records:
        if record.kind not in _CORRECTION_KINDS:
            if current is None:
                current = record.purchase_record_id
        elif record.corrects_record_id == current and current is not None:
            current = record.purchase_record_id if record.kind == "correction" else None
    return current is not None


@router.post(
    "/purchase-records",
    status_code=201,
    response_model=PurchaseRecordOut,
    responses={
        200: {"model": PurchaseRecordOut},
        404: {"model": PurchaseError},
        409: {"model": PurchaseError},
        422: {"model": PurchaseError},
    },
)
async def create_purchase_record(
    payload: PurchaseRecordIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> PurchaseRecordOut | JSONResponse:
    body_hash = _payload_hash(await request.json())

    if session.get(Race, payload.race_id) is None:
        return _error(404, "race_not_found", f"race {payload.race_id} not found")

    invalid_bet = _invalid_bet_message(payload.bets)
    if invalid_bet is not None:
        return _error(422, "invalid_bet", invalid_bet)

    if payload.kind in _PRESENTED_KINDS and (
        payload.presented_snapshot is None or payload.prediction_run_id is None
    ):
        return _error(
            422,
            "invalid_shape",
            "presented_snapshot and prediction_run_id are required for presented records",
        )
    if payload.kind in _NO_SNAPSHOT_KINDS and payload.presented_snapshot is not None:
        return _error(
            422,
            "invalid_shape",
            f"presented_snapshot must be null for {payload.kind}",
        )
    if payload.kind not in _CORRECTION_KINDS and payload.corrects_record_id is not None:
        return _error(
            422,
            "invalid_shape",
            "corrects_record_id is only valid for correction or void",
        )
    if payload.kind in _EMPTY_BET_KINDS and payload.bets:
        return _error(422, "invalid_shape", f"bets must be empty for {payload.kind}")

    if payload.kind in _CORRECTION_KINDS:
        target = (
            session.get(PurchaseRecord, payload.corrects_record_id)
            if payload.corrects_record_id is not None
            else None
        )
        if target is None or target.race_id != payload.race_id:
            return _error(
                422,
                "invalid_correction",
                "corrects_record_id must reference a record for the same race",
            )

    if payload.prediction_run_id is not None:
        prediction_run = session.get(PredictionRun, payload.prediction_run_id)
        if prediction_run is None or prediction_run.race_id != payload.race_id:
            return _error(
                422,
                "run_race_mismatch",
                "prediction_run_id must reference a run for the same race",
            )

    existing_request = session.scalar(
        select(PurchaseRecord).where(
            PurchaseRecord.client_request_id == payload.client_request_id
        )
    )
    if (
        payload.kind not in _CORRECTION_KINDS
        and existing_request is None
        and _has_effective_record(session, payload.race_id)
    ):
        return _error(
            422,
            "already_recorded",
            "this race already has an effective purchase record; use a correction",
        )

    if existing_request is not None:
        if existing_request.payload_hash != body_hash:
            return _error(
                409,
                "request_id_conflict",
                "client_request_id has already been used with a different payload",
            )
        response.status_code = 200
        return _response(existing_request, replayed=True)

    result_count = session.scalar(
        select(func.count())
        .select_from(RaceResult)
        .where(RaceResult.race_id == payload.race_id)
    )
    record = PurchaseRecord(
        race_id=payload.race_id,
        kind=payload.kind,
        bets=_stored_bets(payload.bets),
        presented_snapshot=payload.presented_snapshot,
        prediction_run_id=payload.prediction_run_id,
        corrects_record_id=payload.corrects_record_id,
        result_pending_at_record=result_count == 0,
        pending_basis_at=datetime.datetime.now(datetime.UTC),
        client_request_id=payload.client_request_id,
        payload_hash=body_hash,
        note=payload.note,
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        concurrent_record = session.scalar(
            select(PurchaseRecord).where(
                PurchaseRecord.client_request_id == payload.client_request_id
            )
        )
        if concurrent_record is None:
            raise
        if concurrent_record.payload_hash != body_hash:
            return _error(
                409,
                "request_id_conflict",
                "client_request_id has already been used with a different payload",
            )
        response.status_code = 200
        return _response(concurrent_record, replayed=True)

    session.refresh(record)
    return _response(record, replayed=False)
