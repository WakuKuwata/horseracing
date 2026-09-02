"""Pure fold from append-only purchase rows to the currently effective record."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

BASE_KINDS = frozenset(
    {
        "as_presented",
        "modified",
        "skipped_presented",
        "no_recommendation",
        "presentation_unavailable",
        "freeform",
    }
)
SPECIAL_KINDS = frozenset({"correction", "void"})
PURCHASE_KINDS = BASE_KINDS | SPECIAL_KINDS

MULTIPLE_BASE_RECORDS = "multiple_base_records"
INVALID_CORRECTION_TARGET = "invalid_correction_target"
INVALID_VOID_TARGET = "invalid_void_target"

Bet = dict[str, object]


@dataclass(frozen=True, slots=True)
class PurchaseRecordRow:
    """Lightweight typed input accepted alongside dictionaries."""

    purchase_record_id: object
    kind: str
    bets: list[Bet]
    corrects_record_id: object | None
    recorded_at: datetime
    result_pending_at_record: bool


class PurchaseRecordLike(Protocol):
    """Structural input type for callers that already own a row dataclass."""

    purchase_record_id: object
    kind: str
    bets: list[Bet]
    corrects_record_id: object | None
    recorded_at: datetime
    result_pending_at_record: bool


@dataclass(frozen=True, slots=True)
class EffectiveRecord:
    """Current state derived from one race's append-only purchase history."""

    record_id: object
    kind: str
    bets: list[Bet]
    base_record_id: object
    n_corrections: int
    was_voided: bool
    result_pending_at_record: bool
    anomalies: list[str]


@dataclass(frozen=True, slots=True)
class _NormalizedRow:
    purchase_record_id: object
    kind: str
    bets: list[Bet]
    corrects_record_id: object | None
    recorded_at: datetime
    result_pending_at_record: bool


def _field(row: PurchaseRecordLike | Mapping[str, object], name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def _normalize(row: PurchaseRecordLike | Mapping[str, object]) -> _NormalizedRow:
    kind = _field(row, "kind")
    bets = _field(row, "bets")
    recorded_at = _field(row, "recorded_at")
    result_pending = _field(row, "result_pending_at_record")

    if not isinstance(kind, str) or kind not in PURCHASE_KINDS:
        raise ValueError(f"unsupported purchase record kind: {kind}")
    if not isinstance(bets, list):
        raise TypeError("bets must be a list")
    if not isinstance(recorded_at, datetime):
        raise TypeError("recorded_at must be a datetime")
    if not isinstance(result_pending, bool):
        raise TypeError("result_pending_at_record must be a bool")

    return _NormalizedRow(
        purchase_record_id=_field(row, "purchase_record_id"),
        kind=kind,
        bets=deepcopy(cast(list[Bet], bets)),
        corrects_record_id=_field(row, "corrects_record_id"),
        recorded_at=recorded_at,
        result_pending_at_record=result_pending,
    )


def _add_anomaly(anomalies: list[str], anomaly: str) -> None:
    if anomaly not in anomalies:
        anomalies.append(anomaly)


def fold_purchase_records(
    rows: Sequence[PurchaseRecordLike | Mapping[str, object]],
) -> EffectiveRecord | None:
    """Fold one race's rows in deterministic chronological order.

    Equal timestamps are ordered by the string form of ``purchase_record_id``.  A correction or
    void only applies when it directly targets the current effective row.  While a record is
    effective, later base rows are ignored and reported as an anomaly.  After a valid void, a new
    base row may start a fresh effective chain.
    """
    ordered = sorted(
        (_normalize(row) for row in rows),
        key=lambda row: (row.recorded_at, str(row.purchase_record_id)),
    )

    current: _NormalizedRow | None = None
    base_record_id: object | None = None
    base_result_pending = False
    n_corrections = 0
    was_voided = False
    anomalies: list[str] = []

    for row in ordered:
        if row.kind in BASE_KINDS:
            if current is not None:
                _add_anomaly(anomalies, MULTIPLE_BASE_RECORDS)
                continue
            current = row
            base_record_id = row.purchase_record_id
            base_result_pending = row.result_pending_at_record
            n_corrections = 0
            continue

        if row.kind == "correction":
            if current is None or row.corrects_record_id != current.purchase_record_id:
                _add_anomaly(anomalies, INVALID_CORRECTION_TARGET)
                continue
            current = row
            n_corrections += 1
            continue

        if current is None or row.corrects_record_id != current.purchase_record_id:
            _add_anomaly(anomalies, INVALID_VOID_TARGET)
            continue
        current = None
        base_record_id = None
        n_corrections = 0
        was_voided = True

    if current is None or base_record_id is None:
        return None

    return EffectiveRecord(
        record_id=current.purchase_record_id,
        kind=current.kind,
        bets=deepcopy(current.bets),
        base_record_id=base_record_id,
        n_corrections=n_corrections,
        was_voided=was_voided,
        result_pending_at_record=base_result_pending,
        anomalies=anomalies,
    )
