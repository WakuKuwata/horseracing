"""Feature 106 US1: append-only purchase-record POST contract."""

from __future__ import annotations

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from horseracing_db.enums import ResultStatus
from horseracing_db.models import (
    Horse,
    ModelVersion,
    PredictionRun,
    PurchaseRecord,
    Race,
    RaceResult,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from horseracing_ops import API_PREFIX
from horseracing_ops.app import app
from horseracing_ops.routers import purchase

pytestmark = pytest.mark.integration

RACE_ID = "202609020101"
OTHER_RACE_ID = "202609020102"
ENDPOINT = f"{API_PREFIX}/purchase-records"


@pytest.fixture(scope="module", autouse=True)
def _register_purchase_router() -> None:
    if not any(getattr(route, "path", None) == ENDPOINT for route in app.routes):
        app.include_router(purchase.router, prefix=API_PREFIX)


def _seed_race_and_run(
    session: Session,
    *,
    race_id: str = RACE_ID,
    model_version: str = "purchase-test-model",
) -> uuid.UUID:
    session.add(
        Race(
            race_id=race_id,
            race_date=datetime.date(2026, 9, 2),
            race_number=int(race_id[-2:]),
        )
    )
    session.add(ModelVersion(model_version=model_version, model_family="test"))
    session.flush()
    run = PredictionRun(
        race_id=race_id,
        model_version=model_version,
        logic_version="purchase-test",
    )
    session.add(run)
    session.commit()
    return run.prediction_run_id


def _as_presented_body(
    run_id: uuid.UUID,
    *,
    request_id: str = "request-as-presented",
) -> dict[str, object]:
    return {
        "race_id": RACE_ID,
        "kind": "as_presented",
        "bets": [
            {
                "bet_type": "win",
                "selection": [3],
                "amount_yen": 500,
                "odds_used": 4.2,
            }
        ],
        "client_request_id": request_id,
        "prediction_run_id": str(run_id),
        "presented_snapshot": {
            "snapshot_schema_version": 1,
            "bets": [{"bet_type": "win", "selection": [3], "amount_yen": 500}],
        },
        "corrects_record_id": None,
        "note": "displayed slip",
    }


def _assert_created(
    response,
    *,
    kind: str,
    bets: list[dict[str, object]],
    result_pending: bool,
) -> dict[str, object]:
    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["purchase_record_id"])
    assert body["race_id"] == RACE_ID
    assert body["kind"] == kind
    assert body["bets"] == bets
    assert body["result_pending_at_record"] is result_pending
    assert datetime.datetime.fromisoformat(body["pending_basis_at"]).tzinfo is not None
    assert datetime.datetime.fromisoformat(body["recorded_at"]).tzinfo is not None
    assert body["replayed"] is False
    return body


def test_as_presented_without_results_is_pending(client: TestClient, session: Session) -> None:
    run_id = _seed_race_and_run(session)
    payload = _as_presented_body(run_id)

    response = client.post(ENDPOINT, json=payload)

    _assert_created(
        response,
        kind="as_presented",
        bets=payload["bets"],
        result_pending=True,
    )


def test_record_after_result_is_not_pending(client: TestClient, session: Session) -> None:
    run_id = _seed_race_and_run(session)
    session.add(Horse(horse_id="result-horse", horse_name="Result Horse"))
    session.add(
        RaceResult(
            race_id=RACE_ID,
            horse_id="result-horse",
            finish_order=1,
            result_status=ResultStatus.FINISHED,
        )
    )
    session.commit()
    payload = _as_presented_body(run_id)

    response = client.post(ENDPOINT, json=payload)

    _assert_created(
        response,
        kind="as_presented",
        bets=payload["bets"],
        result_pending=False,
    )


@pytest.mark.parametrize(
    ("kind", "bets", "snapshot", "run_required"),
    [
        ("skipped_presented", [], {"snapshot_schema_version": 1, "bets": []}, True),
        ("freeform", [{"bet_type": "place", "selection": [5], "amount_yen": 300,
                       "odds_used": None}], None, False),
    ],
)
def test_supported_record_shapes(
    client: TestClient,
    session: Session,
    kind: str,
    bets: list[dict[str, object]],
    snapshot: dict[str, object] | None,
    run_required: bool,
) -> None:
    run_id = _seed_race_and_run(session)
    payload = {
        "race_id": RACE_ID,
        "kind": kind,
        "bets": bets,
        "client_request_id": f"request-{kind}",
        "prediction_run_id": str(run_id) if run_required else None,
        "presented_snapshot": snapshot,
        "corrects_record_id": None,
        "note": None,
    }

    response = client.post(ENDPOINT, json=payload)

    _assert_created(response, kind=kind, bets=bets, result_pending=True)


def test_correction_accepts_existing_target_and_rejects_missing_target(
    client: TestClient,
    session: Session,
) -> None:
    run_id = _seed_race_and_run(session)
    original = client.post(ENDPOINT, json=_as_presented_body(run_id)).json()
    correction_bets = [
        {"bet_type": "exacta", "selection": [3, 1], "amount_yen": 700,
         "odds_used": 12.5}
    ]
    correction = {
        "race_id": RACE_ID,
        "kind": "correction",
        "bets": correction_bets,
        "client_request_id": "request-correction",
        "prediction_run_id": None,
        "presented_snapshot": None,
        "corrects_record_id": original["purchase_record_id"],
        "note": "corrected amount",
    }

    accepted = client.post(ENDPOINT, json=correction)
    _assert_created(
        accepted,
        kind="correction",
        bets=correction_bets,
        result_pending=True,
    )

    correction["client_request_id"] = "request-missing-correction"
    correction["corrects_record_id"] = str(uuid.uuid4())
    rejected = client.post(ENDPOINT, json=correction)
    assert rejected.status_code == 422
    assert rejected.json() == {
        "detail": {
            "code": "invalid_correction",
            "message": "corrects_record_id must reference a record for the same race",
        }
    }


def test_duplicate_record_rejected_but_correction_allowed(
    client: TestClient,
    session: Session,
) -> None:
    run_id = _seed_race_and_run(session)
    first = client.post(ENDPOINT, json=_as_presented_body(run_id)).json()
    duplicate = _as_presented_body(run_id, request_id="request-duplicate")

    rejected = client.post(ENDPOINT, json=duplicate)
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "already_recorded"
    assert rejected.json()["detail"]["message"] == (
        "this race already has an effective purchase record; use a correction"
    )

    correction = {
        "race_id": RACE_ID,
        "kind": "correction",
        "bets": [{"bet_type": "win", "selection": [2], "amount_yen": 600,
                  "odds_used": 5.0}],
        "client_request_id": "request-correction-after-duplicate",
        "prediction_run_id": None,
        "presented_snapshot": None,
        "corrects_record_id": first["purchase_record_id"],
        "note": None,
    }
    accepted = client.post(ENDPOINT, json=correction)
    _assert_created(
        accepted,
        kind="correction",
        bets=correction["bets"],
        result_pending=True,
    )


def test_idempotent_replay_and_request_id_conflict(
    client: TestClient,
    session: Session,
) -> None:
    run_id = _seed_race_and_run(session)
    payload = _as_presented_body(run_id, request_id="request-idempotent")
    created = client.post(ENDPOINT, json=payload)
    created_body = created.json()

    replay = client.post(ENDPOINT, json=payload)

    assert replay.status_code == 200
    assert replay.json() == {
        "purchase_record_id": created_body["purchase_record_id"],
        "race_id": RACE_ID,
        "kind": "as_presented",
        "bets": payload["bets"],
        "result_pending_at_record": True,
        "pending_basis_at": created_body["pending_basis_at"],
        "recorded_at": created_body["recorded_at"],
        "replayed": True,
    }
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(PurchaseRecord)) == 1

    changed = dict(payload)
    changed["note"] = "different payload"
    conflict = client.post(ENDPOINT, json=changed)
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": {
            "code": "request_id_conflict",
            "message": "client_request_id has already been used with a different payload",
        }
    }


@pytest.mark.parametrize(
    ("invalid_bet", "expected_message"),
    [
        (
            {"bet_type": "win", "selection": [1], "amount_yen": 150, "odds_used": 2.0},
            "bets[0].amount_yen must be a positive multiple of 100",
        ),
        (
            {
                "bet_type": "quinella",
                "selection": [4, 2],
                "amount_yen": 200,
                "odds_used": 8.0,
            },
            "bets[0].selection must be in ascending canonical order",
        ),
    ],
)
def test_invalid_bet_rejected(
    client: TestClient,
    session: Session,
    invalid_bet: dict[str, object],
    expected_message: str,
) -> None:
    run_id = _seed_race_and_run(session)
    payload = _as_presented_body(run_id)
    payload["bets"] = [invalid_bet]

    response = client.post(ENDPOINT, json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "invalid_bet", "message": expected_message}
    }


def test_prediction_run_must_belong_to_race(client: TestClient, session: Session) -> None:
    session.add(
        Race(
            race_id=RACE_ID,
            race_date=datetime.date(2026, 9, 2),
            race_number=1,
        )
    )
    session.add(
        Race(
            race_id=OTHER_RACE_ID,
            race_date=datetime.date(2026, 9, 2),
            race_number=2,
        )
    )
    session.add(ModelVersion(model_version="mismatch-model", model_family="test"))
    session.flush()
    other_run = PredictionRun(
        race_id=OTHER_RACE_ID,
        model_version="mismatch-model",
        logic_version="purchase-test",
    )
    session.add(other_run)
    session.commit()
    payload = _as_presented_body(other_run.prediction_run_id)

    response = client.post(ENDPOINT, json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "run_race_mismatch",
            "message": "prediction_run_id must reference a run for the same race",
        }
    }


def test_void_of_correction_reopens_the_race(client: TestClient, session: Session) -> None:
    """Regression (found live): base -> correction -> void must make the race recordable.

    The double-record guard must replay the chain with the api-fold semantics — a correction
    moves the effective id, so the void targets the CORRECTION row. A guard that only checks
    voids against base rows locks the race out of re-recording forever.
    """
    run_id = _seed_race_and_run(session)
    session.commit()

    base = client.post(ENDPOINT, json=_as_presented_body(run_id))
    assert base.status_code == 201, base.text
    base_id = base.json()["purchase_record_id"]

    correction = client.post(ENDPOINT, json={
        "race_id": RACE_ID,
        "kind": "correction",
        "bets": [{"bet_type": "win", "selection": [1], "amount_yen": 200, "odds_used": 2.5}],
        "corrects_record_id": base_id,
        "client_request_id": str(uuid.uuid4()),
    })
    assert correction.status_code == 201, correction.text
    correction_id = correction.json()["purchase_record_id"]

    void = client.post(ENDPOINT, json={
        "race_id": RACE_ID,
        "kind": "void",
        "bets": [],
        "corrects_record_id": correction_id,
        "client_request_id": str(uuid.uuid4()),
    })
    assert void.status_code == 201, void.text

    reopened = client.post(
        ENDPOINT, json=_as_presented_body(run_id, request_id=str(uuid.uuid4()))
    )
    assert reopened.status_code == 201, reopened.text
