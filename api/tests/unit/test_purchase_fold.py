"""Unit tests for deterministic folding of append-only purchase records."""

from datetime import UTC, datetime, timedelta

from horseracing_api.purchase_fold import (
    MULTIPLE_BASE_RECORDS,
    EffectiveRecord,
    PurchaseRecordRow,
    fold_purchase_records,
)

START = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)


def _row(
    record_id: str,
    kind: str,
    minute: int,
    *,
    corrects: str | None = None,
    amount: int = 100,
    pending: bool = True,
) -> dict[str, object]:
    return {
        "purchase_record_id": record_id,
        "kind": kind,
        "bets": [{"bet_type": "win", "selection": [3], "amount_yen": amount}],
        "corrects_record_id": corrects,
        "recorded_at": START + timedelta(minutes=minute),
        "result_pending_at_record": pending,
    }


def test_correction_chain_replaces_current_row() -> None:
    rows = [
        _row("base", "modified", 0, amount=100, pending=True),
        _row("correction-1", "correction", 1, corrects="base", amount=200, pending=False),
        _row(
            "correction-2",
            "correction",
            2,
            corrects="correction-1",
            amount=300,
            pending=False,
        ),
    ]

    effective = fold_purchase_records(rows)

    assert effective == EffectiveRecord(
        record_id="correction-2",
        kind="correction",
        bets=[{"bet_type": "win", "selection": [3], "amount_yen": 300}],
        base_record_id="base",
        n_corrections=2,
        was_voided=False,
        result_pending_at_record=True,  # Frozen from the base, not either correction.
        anomalies=[],
    )


def test_new_base_after_void_starts_a_fresh_chain() -> None:
    rows = [
        _row("base-1", "as_presented", 0),
        _row("correction", "correction", 1, corrects="base-1", amount=200),
        _row("void", "void", 2, corrects="correction", amount=0),
        _row("base-2", "freeform", 3, amount=500, pending=False),
    ]

    effective = fold_purchase_records(rows)

    assert effective is not None
    assert effective.record_id == "base-2"
    assert effective.base_record_id == "base-2"
    assert effective.n_corrections == 0  # The voided chain's correction does not carry forward.
    assert effective.was_voided is True
    assert effective.result_pending_at_record is False


def test_valid_void_leaves_no_effective_record() -> None:
    rows = [
        _row("base", "no_recommendation", 0),
        _row("void", "void", 1, corrects="base", amount=0),
    ]

    assert fold_purchase_records(rows) is None
    assert fold_purchase_records([]) is None


def test_additional_base_is_ignored_and_flagged() -> None:
    rows = [
        _row("first", "as_presented", 0, amount=100),
        _row("second", "freeform", 1, amount=900),
    ]

    effective = fold_purchase_records(rows)

    assert effective is not None
    assert effective.record_id == "first"
    assert effective.bets[0]["amount_yen"] == 100
    assert effective.anomalies == [MULTIPLE_BASE_RECORDS]


def test_input_order_does_not_change_chronological_fold() -> None:
    base = PurchaseRecordRow(
        purchase_record_id="base",
        kind="modified",
        bets=[{"bet_type": "win", "selection": [3], "amount_yen": 100}],
        corrects_record_id=None,
        recorded_at=START,
        result_pending_at_record=True,
    )
    correction = PurchaseRecordRow(
        purchase_record_id="correction",
        kind="correction",
        bets=[{"bet_type": "win", "selection": [3], "amount_yen": 400}],
        corrects_record_id="base",
        recorded_at=START + timedelta(minutes=1),
        result_pending_at_record=False,
    )

    chronological = fold_purchase_records([base, correction])
    reversed_input = fold_purchase_records([correction, base])

    assert reversed_input == chronological
    assert chronological is not None
    assert chronological.record_id == "correction"
    assert chronological.n_corrections == 1
