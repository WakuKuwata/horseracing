"""Recovery for pre-086 duplicate chaos snapshots.

The command intentionally uses catalog-driven SQL instead of the current ORM. It runs after
migration 0013 has rolled back, so the database is still at 0012 and does not have the provenance
columns present in the current ``ChaosSnapshot`` model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, bindparam, text

from .session import create_db_engine

_SNAPSHOT_TABLE = "chaos_snapshots"
_SNAPSHOT_QUARANTINE_TABLE = "chaos_snapshots_quarantine"
_READOUT_TABLE = "chaos_readouts"
_READOUT_QUARANTINE_TABLE = "chaos_readouts_quarantine"
_QUARANTINE_REASON = "unique_race_id_backfill"


class DedupeChaosSnapshotsError(RuntimeError):
    """Base class for deterministic duplicate-recovery failures."""


class DuplicateChaosSnapshotsError(DedupeChaosSnapshotsError):
    """Migration-blocking duplicate race IDs and their snapshot counts."""

    def __init__(self, duplicates: Sequence[tuple[str, int]]) -> None:
        self.duplicates = tuple((str(race_id), int(count)) for race_id, count in duplicates)
        details = ", ".join(
            f"{race_id} ({count} rows)" for race_id, count in self.duplicates
        )
        super().__init__(
            "chaos_snapshots contains duplicate race_ids, so UNIQUE(race_id) cannot be "
            f"created: {details}. Run `python -m horseracing_db "
            "dedupe-chaos-snapshots --apply`, then re-run `alembic upgrade head`."
        )


@dataclass(frozen=True)
class DuplicateRace:
    race_id: str
    snapshot_count: int


@dataclass(frozen=True)
class DedupeResult:
    applied: bool
    duplicate_races: tuple[DuplicateRace, ...]
    quarantined_snapshots: int
    quarantined_readouts: int


@dataclass(frozen=True)
class _ColumnSpec:
    name: str
    udt_schema: str
    udt_name: str


def _column_specs(connection: Connection, table_name: str) -> tuple[_ColumnSpec, ...]:
    rows = connection.execute(
        text(
            """
            SELECT column_name, udt_schema, udt_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            ORDER BY ordinal_position
            """
        ),
        {"table_name": table_name},
    )
    return tuple(
        _ColumnSpec(
            name=str(row.column_name),
            udt_schema=str(row.udt_schema),
            udt_name=str(row.udt_name),
        )
        for row in rows
    )


def _require_columns(connection: Connection, table_name: str) -> tuple[_ColumnSpec, ...]:
    columns = _column_specs(connection, table_name)
    if not columns:
        raise DedupeChaosSnapshotsError(
            f"required table {table_name!r} does not exist in the current schema"
        )
    return columns


def _quote_identifier(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _duplicate_races(connection: Connection) -> tuple[DuplicateRace, ...]:
    rows = connection.execute(
        text(
            """
            SELECT race_id, count(*) AS snapshot_count
            FROM chaos_snapshots
            GROUP BY race_id
            HAVING count(*) > 1
            ORDER BY race_id
            """
        )
    )
    return tuple(
        DuplicateRace(str(row.race_id), int(row.snapshot_count)) for row in rows
    )


def _snapshot_ids_to_quarantine(connection: Connection) -> tuple[object, ...]:
    rows = connection.scalars(
        text(
            """
            WITH ranked AS (
                SELECT
                    chaos_snapshot_id,
                    row_number() OVER (
                        PARTITION BY race_id
                        ORDER BY
                            CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                            captured_at DESC,
                            chaos_snapshot_id DESC
                    ) AS keep_rank
                FROM chaos_snapshots
            )
            SELECT chaos_snapshot_id
            FROM ranked
            WHERE keep_rank > 1
            ORDER BY chaos_snapshot_id
            """
        )
    )
    return tuple(rows)


def _count_dependent_readouts(
    connection: Connection, snapshot_ids: Sequence[object]
) -> int:
    if not snapshot_ids:
        return 0
    statement = text(
        """
        SELECT count(*)
        FROM chaos_readouts
        WHERE chaos_snapshot_id IN :snapshot_ids
        """
    ).bindparams(bindparam("snapshot_ids", expanding=True))
    return int(connection.scalar(statement, {"snapshot_ids": tuple(snapshot_ids)}) or 0)


def _ensure_quarantine_table(
    connection: Connection,
    *,
    source_table: str,
    quarantine_table: str,
    source_columns: tuple[_ColumnSpec, ...],
) -> None:
    existing_columns = _column_specs(connection, quarantine_table)
    metadata_columns = (
        _ColumnSpec("quarantined_at", "pg_catalog", "timestamptz"),
        _ColumnSpec("quarantine_reason", "pg_catalog", "text"),
    )
    expected_columns = source_columns + metadata_columns

    if existing_columns:
        if existing_columns != expected_columns:
            expected_names = [column.name for column in expected_columns]
            actual_names = [column.name for column in existing_columns]
            raise DedupeChaosSnapshotsError(
                f"{quarantine_table} has incompatible columns; "
                f"expected {expected_names}, found {actual_names}"
            )
        return

    source = _quote_identifier(connection, source_table)
    quarantine = _quote_identifier(connection, quarantine_table)
    source_projection = ", ".join(
        f"source.{_quote_identifier(connection, column.name)}"
        for column in source_columns
    )
    connection.exec_driver_sql(
        f"""
        CREATE TABLE {quarantine} AS
        SELECT
            {source_projection},
            CURRENT_TIMESTAMP AS quarantined_at,
            CAST('{_QUARANTINE_REASON}' AS text) AS quarantine_reason
        FROM {source} AS source
        WITH NO DATA
        """
    )
    connection.exec_driver_sql(
        f"""
        ALTER TABLE {quarantine}
        ALTER COLUMN quarantined_at SET NOT NULL,
        ALTER COLUMN quarantine_reason SET NOT NULL
        """
    )


def _copy_filtered_rows(
    connection: Connection,
    *,
    source_table: str,
    quarantine_table: str,
    source_columns: tuple[_ColumnSpec, ...],
    filter_column: str,
    filter_values: Sequence[object],
) -> int:
    if not filter_values:
        return 0
    source = _quote_identifier(connection, source_table)
    quarantine = _quote_identifier(connection, quarantine_table)
    destination_columns = ", ".join(
        [
            *(_quote_identifier(connection, column.name) for column in source_columns),
            "quarantined_at",
            "quarantine_reason",
        ]
    )
    source_projection = ", ".join(
        f"source.{_quote_identifier(connection, column.name)}"
        for column in source_columns
    )
    filter_identifier = _quote_identifier(connection, filter_column)
    statement = text(
        f"""
        INSERT INTO {quarantine} ({destination_columns})
        SELECT
            {source_projection},
            CURRENT_TIMESTAMP,
            :quarantine_reason
        FROM {source} AS source
        WHERE source.{filter_identifier} IN :filter_values
        """
    ).bindparams(bindparam("filter_values", expanding=True))
    result = connection.execute(
        statement,
        {
            "filter_values": tuple(filter_values),
            "quarantine_reason": _QUARANTINE_REASON,
        },
    )
    return int(result.rowcount)


def _delete_filtered_rows(
    connection: Connection,
    *,
    source_table: str,
    filter_column: str,
    filter_values: Sequence[object],
) -> int:
    if not filter_values:
        return 0
    source = _quote_identifier(connection, source_table)
    filter_identifier = _quote_identifier(connection, filter_column)
    statement = text(
        f"""
        DELETE FROM {source}
        WHERE {filter_identifier} IN :filter_values
        """
    ).bindparams(bindparam("filter_values", expanding=True))
    result = connection.execute(
        statement,
        {"filter_values": tuple(filter_values)},
    )
    return int(result.rowcount)


def _dry_run(connection: Connection) -> DedupeResult:
    _require_columns(connection, _SNAPSHOT_TABLE)
    duplicates = _duplicate_races(connection)
    snapshot_ids = _snapshot_ids_to_quarantine(connection)
    return DedupeResult(
        applied=False,
        duplicate_races=duplicates,
        quarantined_snapshots=len(snapshot_ids),
        quarantined_readouts=_count_dependent_readouts(connection, snapshot_ids),
    )


def _apply(connection: Connection) -> DedupeResult:
    # Hold both audit tables stable while rows are copied and deleted. PostgreSQL releases the
    # locks together with the transaction after every count has been verified.
    connection.exec_driver_sql(
        "LOCK TABLE chaos_snapshots, chaos_readouts IN ACCESS EXCLUSIVE MODE"
    )
    snapshot_columns = _require_columns(connection, _SNAPSHOT_TABLE)
    readout_columns = _require_columns(connection, _READOUT_TABLE)
    duplicates = _duplicate_races(connection)
    snapshot_ids = _snapshot_ids_to_quarantine(connection)
    if not snapshot_ids:
        return DedupeResult(
            applied=True,
            duplicate_races=duplicates,
            quarantined_snapshots=0,
            quarantined_readouts=0,
        )

    dependent_readouts = _count_dependent_readouts(connection, snapshot_ids)
    _ensure_quarantine_table(
        connection,
        source_table=_SNAPSHOT_TABLE,
        quarantine_table=_SNAPSHOT_QUARANTINE_TABLE,
        source_columns=snapshot_columns,
    )
    if dependent_readouts:
        # Feature 084 wrote one append-only readout for each snapshot. Preserve those audit rows
        # in a companion quarantine before deleting them so the snapshot FK can be satisfied.
        _ensure_quarantine_table(
            connection,
            source_table=_READOUT_TABLE,
            quarantine_table=_READOUT_QUARANTINE_TABLE,
            source_columns=readout_columns,
        )
        copied_readouts = _copy_filtered_rows(
            connection,
            source_table=_READOUT_TABLE,
            quarantine_table=_READOUT_QUARANTINE_TABLE,
            source_columns=readout_columns,
            filter_column="chaos_snapshot_id",
            filter_values=snapshot_ids,
        )
        if copied_readouts != dependent_readouts:
            raise DedupeChaosSnapshotsError(
                "readout quarantine copy count mismatch: "
                f"expected {dependent_readouts}, copied {copied_readouts}"
            )
        deleted_readouts = _delete_filtered_rows(
            connection,
            source_table=_READOUT_TABLE,
            filter_column="chaos_snapshot_id",
            filter_values=snapshot_ids,
        )
        if deleted_readouts != dependent_readouts:
            raise DedupeChaosSnapshotsError(
                "readout delete count mismatch after quarantine: "
                f"expected {dependent_readouts}, deleted {deleted_readouts}"
            )

    copied_snapshots = _copy_filtered_rows(
        connection,
        source_table=_SNAPSHOT_TABLE,
        quarantine_table=_SNAPSHOT_QUARANTINE_TABLE,
        source_columns=snapshot_columns,
        filter_column="chaos_snapshot_id",
        filter_values=snapshot_ids,
    )
    if copied_snapshots != len(snapshot_ids):
        raise DedupeChaosSnapshotsError(
            "snapshot quarantine copy count mismatch: "
            f"expected {len(snapshot_ids)}, copied {copied_snapshots}"
        )
    deleted_snapshots = _delete_filtered_rows(
        connection,
        source_table=_SNAPSHOT_TABLE,
        filter_column="chaos_snapshot_id",
        filter_values=snapshot_ids,
    )
    if deleted_snapshots != len(snapshot_ids):
        raise DedupeChaosSnapshotsError(
            "snapshot delete count mismatch after quarantine: "
            f"expected {len(snapshot_ids)}, deleted {deleted_snapshots}"
        )

    return DedupeResult(
        applied=True,
        duplicate_races=duplicates,
        quarantined_snapshots=copied_snapshots,
        quarantined_readouts=dependent_readouts,
    )


def dedupe_chaos_snapshots(
    *,
    apply: bool = False,
    engine: Engine | None = None,
) -> DedupeResult:
    """Report duplicate snapshots, or atomically quarantine and remove extras."""

    owned_engine = engine is None
    active_engine = engine or create_db_engine()
    try:
        if apply:
            with active_engine.begin() as connection:
                return _apply(connection)
        with active_engine.connect() as connection:
            return _dry_run(connection)
    finally:
        if owned_engine:
            active_engine.dispose()


def format_dedupe_result(result: DedupeResult) -> str:
    """Render a concise, stable operator summary."""

    mode = "APPLIED" if result.applied else "DRY RUN"
    if not result.duplicate_races:
        return f"{mode}: no duplicate chaos_snapshots race_ids found"
    races = ", ".join(
        f"{duplicate.race_id} ({duplicate.snapshot_count} rows)"
        for duplicate in result.duplicate_races
    )
    return (
        f"{mode}: duplicates={races}; "
        f"snapshots_to_quarantine={result.quarantined_snapshots}; "
        f"readouts_to_quarantine={result.quarantined_readouts}"
    )
