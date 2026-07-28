"""Feature 086: capture provenance, throttling state, and duplicate recovery."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.schema import UniqueConstraint

from horseracing_db.models import ChaosSnapshot, FetchThrottleState

pytestmark = pytest.mark.integration

DB_DIR = Path(__file__).resolve().parents[2]
_REVISION_0012 = "0012_chaos_readout"
_QUARANTINE_TABLE = "chaos_snapshots_quarantine"
_READOUT_QUARANTINE_TABLE = "chaos_readouts_quarantine"
_SNAPSHOT_COLUMNS_0012 = {
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
    "created_at",
}
_READOUT_COLUMNS_0012 = {
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
_FIELD = [
    {
        "horse_id": "H0001",
        "horse_number": 1,
        "popularity": 1,
        "odds": "2.5",
    }
]


def _insert_race(connection, race_id: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO races (race_id, race_number, race_date)
            VALUES (:race_id, 1, DATE '2026-07-26')
            """
        ),
        {"race_id": race_id},
    )


def _insert_snapshot_0012(
    connection,
    *,
    race_id: str,
    captured_at: datetime.datetime,
    status: str,
    digest: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO chaos_snapshots (
                race_id,
                captured_at,
                source,
                seconds_to_post,
                capture_strength,
                field,
                n,
                content_digest,
                status,
                void_reason
            )
            VALUES (
                :race_id,
                :captured_at,
                'netkeiba',
                1800,
                'confirmatory',
                CAST(:field AS jsonb),
                1,
                :digest,
                :status,
                :void_reason
            )
            """
        ),
        {
            "race_id": race_id,
            "captured_at": captured_at,
            "field": json.dumps(_FIELD),
            "digest": digest,
            "status": status,
            "void_reason": "field_changed" if status == "void" else None,
        },
    )


def _insert_snapshot_0013(
    connection,
    *,
    race_id: str,
    status: str = "active",
    trigger: str = "predict_manual",
    include_trigger: bool = True,
) -> None:
    trigger_column = ", capture_trigger" if include_trigger else ""
    trigger_value = ", :trigger" if include_trigger else ""
    connection.execute(
        text(
            f"""
            INSERT INTO chaos_snapshots (
                race_id,
                captured_at,
                source,
                seconds_to_post,
                capture_strength,
                field,
                n,
                content_digest,
                status,
                void_reason,
                capture_policy_version
                {trigger_column}
            )
            VALUES (
                :race_id,
                :captured_at,
                'netkeiba',
                1800,
                'confirmatory',
                CAST(:field AS jsonb),
                1,
                :digest,
                :status,
                :void_reason,
                'capture_policy_v1'
                {trigger_value}
            )
            """
        ),
        {
            "race_id": race_id,
            "captured_at": datetime.datetime(2026, 7, 26, 5, 30, tzinfo=datetime.UTC),
            "field": json.dumps(_FIELD),
            "digest": f"digest-{race_id}-{status}",
            "status": status,
            "void_reason": "field_changed" if status == "void" else None,
            "trigger": trigger,
        },
    )


def _insert_readout(connection, snapshot_id) -> None:
    connection.execute(
        text(
            """
            INSERT INTO chaos_readouts (
                chaos_snapshot_id,
                artifact_version,
                artifact_digest,
                band,
                band_axis,
                p_s_ge_20,
                p_himo_are,
                p_total_collapse,
                raw_p_s_ge_20,
                raw_p_himo_are,
                raw_p_total_collapse,
                expected_s,
                structural_zeros
            )
            VALUES (
                :snapshot_id,
                'chaosbands-v1',
                'artifact-digest',
                't3_mid',
                'p_s_ge_20',
                :p_s_ge_20,
                0.12,
                0.03,
                0.06,
                0.08,
                0.03,
                12.4,
                '{}'::jsonb
            )
            """
        ),
        {"snapshot_id": snapshot_id, "p_s_ge_20": Decimal("0.10")},
    )


def _run_dedupe_cli(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "horseracing_db",
            "dedupe-chaos-snapshots",
            *arguments,
        ],
        cwd=DB_DIR,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


def test_0013_upgrade_downgrade_upgrade_preserves_existing_tables(
    alembic_cfg, engine, _migrated
):
    tables_at_head = set(inspect(engine).get_table_names())
    assert "fetch_throttle_state" in tables_at_head
    assert _QUARANTINE_TABLE not in tables_at_head
    throttle_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("fetch_throttle_state")
    }
    assert set(throttle_columns) == {
        "domain",
        "next_allowed_at",
        "blocked_until",
        "block_reason",
        "updated_at",
    }
    assert throttle_columns["domain"]["nullable"] is False
    assert throttle_columns["next_allowed_at"]["nullable"] is False
    assert throttle_columns["blocked_until"]["nullable"] is True
    assert throttle_columns["block_reason"]["nullable"] is True
    assert throttle_columns["updated_at"]["nullable"] is False

    try:
        command.downgrade(alembic_cfg, _REVISION_0012)
        tables_at_0012 = set(inspect(engine).get_table_names())
        assert tables_at_0012 == tables_at_head - {"fetch_throttle_state"}
        assert {
            column["name"] for column in inspect(engine).get_columns("chaos_snapshots")
        } == _SNAPSHOT_COLUMNS_0012

        command.upgrade(alembic_cfg, "head")
        assert set(inspect(engine).get_table_names()) == tables_at_head
        assert {
            "capture_trigger",
            "capture_policy_version",
        }.issubset(
            column["name"] for column in inspect(engine).get_columns("chaos_snapshots")
        )
    finally:
        command.upgrade(alembic_cfg, "head")


def test_orm_metadata_keeps_unconditional_unique_and_has_no_quarantine_model():
    unique_constraints = {
        constraint.name
        for constraint in ChaosSnapshot.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_chaos_snapshots_race_id" in unique_constraints
    assert set(FetchThrottleState.__table__.columns.keys()) == {
        "domain",
        "next_allowed_at",
        "blocked_until",
        "block_reason",
        "updated_at",
    }
    assert _QUARANTINE_TABLE not in ChaosSnapshot.metadata.tables


def test_capture_trigger_check_and_not_null_are_enforced(engine):
    unknown_race_id = "202607260111"
    missing_race_id = "202607260112"
    with engine.begin() as connection:
        _insert_race(connection, unknown_race_id)
        _insert_race(connection, missing_race_id)

    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(
            IntegrityError,
            match="ck_chaos_snapshots_capture_trigger",
        ):
            _insert_snapshot_0013(
                connection,
                race_id=unknown_race_id,
                trigger="not_a_real_trigger",
            )
        transaction.rollback()

    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError, match="capture_trigger"):
            _insert_snapshot_0013(
                connection,
                race_id=missing_race_id,
                include_trigger=False,
            )
        transaction.rollback()


def test_0012_partial_index_and_readout_update_trigger_survive_0013(engine):
    race_id = "202607260121"
    with engine.begin() as connection:
        index_definition = connection.scalar(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'chaos_snapshots'
                  AND indexname = 'uq_chaos_snapshots_active_race_id'
                """
            )
        )
        assert index_definition is not None
        assert "UNIQUE INDEX" in index_definition
        assert "WHERE (status = 'active'::text)" in index_definition

        triggers = set(
            connection.execute(
                text(
                    """
                    SELECT tables.relname, triggers.tgname
                    FROM pg_trigger AS triggers
                    JOIN pg_class AS tables ON tables.oid = triggers.tgrelid
                    WHERE NOT triggers.tgisinternal
                      AND tables.relname IN ('chaos_readouts', 'fetch_throttle_state')
                    """
                )
            )
        )
        assert triggers == {
            ("chaos_readouts", "trg_chaos_readouts_reject_update")
        }

        connection.execute(
            text(
                """
                INSERT INTO fetch_throttle_state (
                    domain,
                    next_allowed_at,
                    blocked_until,
                    block_reason,
                    updated_at
                )
                VALUES (
                    'https://race.netkeiba.com',
                    now(),
                    NULL,
                    NULL,
                    now()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE fetch_throttle_state
                SET blocked_until = now() + interval '30 minutes',
                    block_reason = 'http_429',
                    updated_at = now()
                WHERE domain = 'https://race.netkeiba.com'
                """
            )
        )
        assert connection.scalar(
            text(
                """
                SELECT block_reason
                FROM fetch_throttle_state
                WHERE domain = 'https://race.netkeiba.com'
                """
            )
        ) == "http_429"

        _insert_race(connection, race_id)
        _insert_snapshot_0013(connection, race_id=race_id)
        snapshot_id = connection.scalar(
            text(
                """
                SELECT chaos_snapshot_id
                FROM chaos_snapshots
                WHERE race_id = :race_id
                """
            ),
            {"race_id": race_id},
        )
        _insert_readout(connection, snapshot_id)

    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(
                text(
                    """
                    UPDATE chaos_readouts
                    SET band = 't3_wild'
                    WHERE chaos_snapshot_id = :snapshot_id
                    """
                ),
                {"snapshot_id": snapshot_id},
            )
        transaction.rollback()


def test_unconditional_race_unique_rejects_second_row_regardless_of_status(engine):
    race_id = "202607260131"
    with engine.begin() as connection:
        _insert_race(connection, race_id)
        _insert_snapshot_0013(connection, race_id=race_id, status="active")

    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError, match="uq_chaos_snapshots_race_id"):
            _insert_snapshot_0013(connection, race_id=race_id, status="void")
        transaction.rollback()


def test_existing_0012_snapshot_is_backfilled_and_new_capture_still_works(
    alembic_cfg, engine, _migrated
):
    legacy_race_id = "202607260141"
    new_race_id = "202607260142"

    try:
        command.downgrade(alembic_cfg, _REVISION_0012)
        with engine.begin() as connection:
            _insert_race(connection, legacy_race_id)
            _insert_snapshot_0012(
                connection,
                race_id=legacy_race_id,
                captured_at=datetime.datetime(2026, 7, 26, 5, 0, tzinfo=datetime.UTC),
                status="active",
                digest="legacy-digest",
            )

        command.upgrade(alembic_cfg, "head")

        with engine.begin() as connection:
            provenance = connection.execute(
                text(
                    """
                    SELECT capture_trigger, capture_policy_version
                    FROM chaos_snapshots
                    WHERE race_id = :race_id
                    """
                ),
                {"race_id": legacy_race_id},
            ).one()
            assert provenance == ("legacy_unknown", "capture_policy_v0")

            _insert_race(connection, new_race_id)
            _insert_snapshot_0013(
                connection,
                race_id=new_race_id,
                trigger="explicit_command",
            )
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM chaos_snapshots
                    WHERE race_id = :race_id
                    """
                ),
                {"race_id": new_race_id},
            ) == 1
    finally:
        command.upgrade(alembic_cfg, "head")


def test_duplicate_upgrade_abort_dedupe_apply_and_reupgrade(
    alembic_cfg, engine, database_url, _migrated
):
    active_race_id = "202607260151"
    newest_race_id = "202607260152"

    try:
        command.downgrade(alembic_cfg, _REVISION_0012)
        with engine.begin() as connection:
            _insert_race(connection, active_race_id)
            _insert_snapshot_0012(
                connection,
                race_id=active_race_id,
                captured_at=datetime.datetime(2026, 7, 26, 5, 0, tzinfo=datetime.UTC),
                status="active",
                digest="active-keep",
            )
            _insert_snapshot_0012(
                connection,
                race_id=active_race_id,
                captured_at=datetime.datetime(2026, 7, 26, 6, 0, tzinfo=datetime.UTC),
                status="void",
                digest="newer-void-quarantine",
            )
            quarantined_snapshot_id = connection.scalar(
                text(
                    """
                    SELECT chaos_snapshot_id
                    FROM chaos_snapshots
                    WHERE content_digest = 'newer-void-quarantine'
                    """
                )
            )
            _insert_readout(connection, quarantined_snapshot_id)

            _insert_race(connection, newest_race_id)
            _insert_snapshot_0012(
                connection,
                race_id=newest_race_id,
                captured_at=datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
                status="void",
                digest="older-void-quarantine",
            )
            _insert_snapshot_0012(
                connection,
                race_id=newest_race_id,
                captured_at=datetime.datetime(2026, 7, 26, 7, 0, tzinfo=datetime.UTC),
                status="void",
                digest="newest-void-keep",
            )

        # The migration is self-contained (it does not import horseracing_db), so assert on the
        # operator-facing message rather than on class identity across that boundary.
        with pytest.raises(RuntimeError) as error:
            command.upgrade(alembic_cfg, "head")
        assert type(error.value).__name__ == "DuplicateChaosSnapshotsError"
        assert active_race_id in str(error.value)
        assert newest_race_id in str(error.value)
        assert "(2 rows)" in str(error.value)
        assert "dedupe-chaos-snapshots --apply" in str(error.value)
        assert "alembic upgrade head" in str(error.value)

        with engine.begin() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == _REVISION_0012
        assert {
            column["name"] for column in inspect(engine).get_columns("chaos_snapshots")
        } == _SNAPSHOT_COLUMNS_0012
        assert "fetch_throttle_state" not in inspect(engine).get_table_names()
        assert _QUARANTINE_TABLE not in inspect(engine).get_table_names()

        dry_run = _run_dedupe_cli(database_url)
        assert dry_run.returncode == 0, dry_run.stderr
        assert "DRY RUN" in dry_run.stdout
        assert "snapshots_to_quarantine=2" in dry_run.stdout
        assert "readouts_to_quarantine=1" in dry_run.stdout
        assert _QUARANTINE_TABLE not in inspect(engine).get_table_names()
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM chaos_snapshots")) == 4

        applied = _run_dedupe_cli(database_url, "--apply")
        assert applied.returncode == 0, applied.stderr
        assert "APPLIED" in applied.stdout
        assert "readouts_to_quarantine=1" in applied.stdout

        with engine.begin() as connection:
            kept = dict(
                connection.execute(
                    text(
                        """
                        SELECT race_id, content_digest
                        FROM chaos_snapshots
                        WHERE race_id IN (:active_race_id, :newest_race_id)
                        """
                    ),
                    {
                        "active_race_id": active_race_id,
                        "newest_race_id": newest_race_id,
                    },
                ).all()
            )
            assert kept == {
                active_race_id: "active-keep",
                newest_race_id: "newest-void-keep",
            }

            quarantined = dict(
                connection.execute(
                    text(
                        f"""
                        SELECT content_digest, quarantine_reason
                        FROM {_QUARANTINE_TABLE}
                        """
                    )
                ).all()
            )
            assert quarantined == {
                "newer-void-quarantine": "unique_race_id_backfill",
                "older-void-quarantine": "unique_race_id_backfill",
            }
            assert connection.scalar(text("SELECT count(*) FROM chaos_readouts")) == 0
            assert connection.scalar(
                text(f"SELECT count(*) FROM {_READOUT_QUARANTINE_TABLE}")
            ) == 1
            assert connection.scalar(
                text(
                    f"""
                    SELECT quarantine_reason
                    FROM {_READOUT_QUARANTINE_TABLE}
                    """
                )
            ) == "unique_race_id_backfill"

        quarantine_columns = {
            column["name"] for column in inspect(engine).get_columns(_QUARANTINE_TABLE)
        }
        assert quarantine_columns == _SNAPSHOT_COLUMNS_0012 | {
            "quarantined_at",
            "quarantine_reason",
        }
        readout_quarantine_columns = {
            column["name"]
            for column in inspect(engine).get_columns(_READOUT_QUARANTINE_TABLE)
        }
        assert readout_quarantine_columns == _READOUT_COLUMNS_0012 | {
            "quarantined_at",
            "quarantine_reason",
        }

        command.upgrade(alembic_cfg, "head")
        with engine.begin() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT DISTINCT capture_trigger, capture_policy_version
                    FROM chaos_snapshots
                    """
                )
            ).all() == [("legacy_unknown", "capture_policy_v0")]
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM chaos_readouts"))
            connection.execute(text("DELETE FROM chaos_snapshots"))
            connection.execute(text(f"DROP TABLE IF EXISTS {_QUARANTINE_TABLE}"))
            connection.execute(
                text(f"DROP TABLE IF EXISTS {_READOUT_QUARANTINE_TABLE}")
            )
        command.upgrade(alembic_cfg, "head")
