"""Feature 084: chaos snapshot/readout schema and database-level invariants."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from horseracing_db.models import ChaosReadout, ChaosSnapshot, Race

pytestmark = pytest.mark.integration

_CHAOS_TABLES = {"chaos_snapshots", "chaos_readouts", "fetch_throttle_state"}
_SNAPSHOT_COLUMNS = {
    "chaos_snapshot_id",
    "race_id",
    "captured_at",
    "source",
    "seconds_to_post",
    "capture_strength",
    "field",
    "n",
    "content_digest",
    "status",
    "void_reason",
    "capture_trigger",
    "capture_policy_version",
    "created_at",
}
_READOUT_COLUMNS = {
    "chaos_readout_id",
    "chaos_snapshot_id",
    "artifact_version",
    "artifact_digest",
    "band",
    "band_axis",
    "p_s_ge_20",
    "p_himo_are",
    "p_total_collapse",
    "raw_p_s_ge_20",
    "raw_p_himo_are",
    "raw_p_total_collapse",
    "expected_s",
    "structural_zeros",
    "computed_at",
}


def _add_race_and_snapshot(session, *, status: str = "active") -> ChaosSnapshot:
    # Flush the parent race before the child snapshot (repo convention, see
    # _prediction_helpers.setup_run): a single combined flush does not guarantee
    # races is inserted before chaos_snapshots and trips the FK.
    session.add(
        Race(
            race_id="202607260101",
            race_number=1,
            race_date=datetime.date(2026, 7, 26),
        )
    )
    session.flush()
    snapshot = ChaosSnapshot(
        race_id="202607260101",
        captured_at=datetime.datetime(2026, 7, 26, 5, 30, tzinfo=datetime.UTC),
        source="netkeiba",
        seconds_to_post=1800,
        capture_strength="confirmatory",
        field=[
            {
                "horse_id": "H0001",
                "horse_number": 1,
                "popularity": 1,
                "odds": "2.5",
            }
        ],
        n=1,
        content_digest="digest-1",
        status=status,
        capture_trigger="predict_manual",
        capture_policy_version="capture_policy_v1",
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def test_0012_upgrade_downgrade_upgrade_preserves_existing_tables(
    alembic_cfg, engine, _migrated
):
    tables_at_head = set(inspect(engine).get_table_names())
    existing_tables = tables_at_head - _CHAOS_TABLES
    assert _CHAOS_TABLES.issubset(tables_at_head)

    try:
        command.downgrade(alembic_cfg, "0011_model_purpose")
        tables_after_downgrade = set(inspect(engine).get_table_names())
        assert tables_after_downgrade == existing_tables

        command.upgrade(alembic_cfg, "head")
        tables_after_upgrade = set(inspect(engine).get_table_names())
        assert tables_after_upgrade - _CHAOS_TABLES == existing_tables
        assert _CHAOS_TABLES.issubset(tables_after_upgrade)
    finally:
        command.upgrade(alembic_cfg, "head")


def test_one_active_snapshot_per_race_is_enforced(session):
    _add_race_and_snapshot(session)
    session.add(
        ChaosSnapshot(
            race_id="202607260101",
            captured_at=datetime.datetime(2026, 7, 26, 5, 35, tzinfo=datetime.UTC),
            source="netkeiba",
            seconds_to_post=1500,
            capture_strength="confirmatory",
            field=[
                {
                    "horse_id": "H0001",
                    "horse_number": 1,
                    "popularity": 1,
                    "odds": "2.4",
                }
            ],
            n=1,
            content_digest="digest-2",
            status="active",
            capture_trigger="predict_manual",
            capture_policy_version="capture_policy_v1",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_chaos_table_columns_match_contract(engine):
    inspector = inspect(engine)
    snapshot_columns = {
        column["name"] for column in inspector.get_columns("chaos_snapshots")
    }
    readout_columns = {
        column["name"] for column in inspector.get_columns("chaos_readouts")
    }

    assert snapshot_columns == _SNAPSHOT_COLUMNS
    assert readout_columns == _READOUT_COLUMNS
    assert "displayed_at" not in readout_columns


def test_chaos_readout_update_is_rejected(session):
    snapshot = _add_race_and_snapshot(session)
    readout = ChaosReadout(
        chaos_snapshot_id=snapshot.chaos_snapshot_id,
        artifact_version="chaosbands-v1",
        artifact_digest="artifact-digest",
        band="t3_mid",
        band_axis="p_s_ge_20",
        p_s_ge_20=Decimal("0.10"),
        p_himo_are=Decimal("0.12"),
        p_total_collapse=Decimal("0.03"),
        raw_p_s_ge_20=Decimal("0.06"),
        raw_p_himo_are=Decimal("0.08"),
        raw_p_total_collapse=Decimal("0.03"),
        expected_s=Decimal("12.4"),
        structural_zeros={},
    )
    session.add(readout)
    session.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(
            text(
                """
                UPDATE chaos_readouts
                SET band = 't3_wild'
                WHERE chaos_readout_id = :readout_id
                """
            ),
            {"readout_id": readout.chaos_readout_id},
        )
    session.rollback()


def test_chaos_indexes_exist(engine):
    snapshot_indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("chaos_snapshots")
    }
    readout_indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("chaos_readouts")
    }

    assert {
        "ix_chaos_snapshots_race_id_captured_at",
        "ix_chaos_snapshots_race_id_status",
        "uq_chaos_snapshots_active_race_id",
    }.issubset(snapshot_indexes)
    assert {
        "ix_chaos_readouts_chaos_snapshot_id",
        "ix_chaos_readouts_computed_at",
    }.issubset(readout_indexes)
    assert snapshot_indexes["uq_chaos_snapshots_active_race_id"]["unique"] is True
