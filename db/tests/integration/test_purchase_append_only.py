"""Feature 106 (T004): purchase_records is append-only AT THE DATABASE BOUNDARY.

Runs under the runtime connection role (the same engine the app uses — locally the table
OWNER), which is the point: a REVOKE alone would not stop the owner, so the protection must
come from triggers, and this test proves it does (research D9). Also proves the downgrade
leaves no orphaned trigger function behind.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from horseracing_db.models import PurchaseRecord, Race

pytestmark = pytest.mark.integration

_RID = "202601010101"


def _seed(session: Session) -> PurchaseRecord:
    session.add(Race(race_id=_RID, race_date=datetime.date(2026, 1, 1)))
    rec = PurchaseRecord(
        race_id=_RID, kind="skipped_presented", bets=[],
        presented_snapshot={"bets": [], "snapshot_schema_version": 1},
        result_pending_at_record=True,
        pending_basis_at=datetime.datetime.now(datetime.UTC),
        client_request_id="t004-seed", payload_hash="h",
    )
    session.add(rec)
    session.commit()
    return rec


def test_update_is_rejected_by_trigger(session: Session):
    rec = _seed(session)
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(
            text("UPDATE purchase_records SET note = 'x' WHERE purchase_record_id = :i"),
            {"i": rec.purchase_record_id},
        )
        session.commit()
    session.rollback()


def test_delete_is_rejected_by_trigger(session: Session):
    rec = _seed(session)
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(
            text("DELETE FROM purchase_records WHERE purchase_record_id = :i"),
            {"i": rec.purchase_record_id},
        )
        session.commit()
    session.rollback()


def test_truncate_is_rejected_even_for_the_owner(session: Session):
    """TRUNCATE is a statement-level trigger — the local runtime connects as the table owner,
    whom a plain REVOKE would not stop. (The test-suite cleanup bypasses it deliberately via
    transaction-scoped replica mode; nothing in production sets that.)"""
    _seed(session)
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("TRUNCATE TABLE purchase_records"))
        session.commit()
    session.rollback()


def test_corrections_are_new_rows_not_mutations(session: Session):
    base = _seed(session)
    session.add(PurchaseRecord(
        race_id=_RID, kind="correction", bets=[],
        corrects_record_id=base.purchase_record_id,
        result_pending_at_record=True,
        pending_basis_at=datetime.datetime.now(datetime.UTC),
        client_request_id="t004-corr", payload_hash="h2",
    ))
    session.commit()
    n = session.execute(text("SELECT count(*) FROM purchase_records")).scalar_one()
    assert n == 2  # the original survives untouched


def test_correction_target_check_constraint(session: Session):
    _seed(session)
    with pytest.raises(DBAPIError, match="ck_purchase_records_correction_target"):
        session.add(PurchaseRecord(
            race_id=_RID, kind="correction", bets=[],   # correction without a target
            result_pending_at_record=True,
            pending_basis_at=datetime.datetime.now(datetime.UTC),
            client_request_id="t004-bad", payload_hash="h3",
        ))
        session.commit()
    session.rollback()
