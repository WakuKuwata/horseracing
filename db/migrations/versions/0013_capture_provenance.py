"""capture provenance and cross-process fetch throttle state (Feature 086)

Revision ID: 0013_capture_provenance
Revises: 0012_chaos_readout
Create Date: 2026-07-28

Add explicit capture provenance to every frozen chaos observation, structurally enforce one
snapshot per race, and add mutable operational state for cross-process fetch throttling.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# NOTE: this migration is deliberately SELF-CONTAINED. A migration is a historical record, so
# importing application code would make replaying history depend on the *current* shape of the
# package — renaming or deleting horseracing_db.dedupe would break `alembic upgrade` from
# scratch. (It already bit us once: for the minutes between this file landing and dedupe.py
# being written, every scrape integration test errored on the missing import.)
class DuplicateChaosSnapshotsError(RuntimeError):
    """Duplicate race_ids block UNIQUE(race_id); recovery runs outside the migration."""

    def __init__(self, duplicates):
        self.duplicates = tuple((str(race_id), int(count)) for race_id, count in duplicates)
        details = ", ".join(f"{race_id} ({count} rows)" for race_id, count in self.duplicates)
        super().__init__(
            "chaos_snapshots contains duplicate race_ids, so UNIQUE(race_id) cannot be "
            f"created: {details}. Run `python -m horseracing_db "
            "dedupe-chaos-snapshots --apply`, then re-run `alembic upgrade head`."
        )


revision = "0013_capture_provenance"
down_revision = "0012_chaos_readout"
branch_labels = None
depends_on = None

_CAPTURE_TRIGGER_CHECK = (
    "capture_trigger IN ("
    "'daily_operational',"
    "'predict_manual',"
    "'predict_auto',"
    "'explicit_command',"
    "'legacy_unknown'"
    ")"
)


def _duplicate_race_counts() -> list[tuple[str, int]]:
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT race_id, count(*) AS snapshot_count
            FROM chaos_snapshots
            GROUP BY race_id
            HAVING count(*) > 1
            ORDER BY race_id
            """
        )
    )
    # Alembic's offline SQL renderer returns no Result object. Online upgrades always receive the
    # rows and therefore always perform the typed duplicate check before adding the UNIQUE.
    if rows is None:
        return []
    return [(str(row.race_id), int(row.snapshot_count)) for row in rows]


def upgrade() -> None:
    # Deliberately use the same sequence for empty and populated databases. Existing rows are
    # unclassifiable because Feature 084 accepted both date and single-race capture paths.
    op.add_column(
        "chaos_snapshots",
        sa.Column("capture_trigger", sa.Text(), nullable=True),
    )
    op.add_column(
        "chaos_snapshots",
        sa.Column("capture_policy_version", sa.Text(), nullable=True),
    )
    op.execute(
        """
        UPDATE chaos_snapshots
        SET capture_trigger = 'legacy_unknown',
            capture_policy_version = 'capture_policy_v0'
        WHERE capture_trigger IS NULL
        """
    )
    op.alter_column("chaos_snapshots", "capture_trigger", nullable=False)
    op.alter_column("chaos_snapshots", "capture_policy_version", nullable=False)
    op.create_check_constraint(
        op.f("ck_chaos_snapshots_capture_trigger"),
        "chaos_snapshots",
        _CAPTURE_TRIGGER_CHECK,
    )

    op.create_table(
        "fetch_throttle_state",
        sa.Column("domain", sa.Text(), primary_key=True),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    duplicates = _duplicate_race_counts()
    if duplicates:
        raise DuplicateChaosSnapshotsError(duplicates)

    op.create_unique_constraint(
        "uq_chaos_snapshots_race_id",
        "chaos_snapshots",
        ["race_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_chaos_snapshots_race_id",
        "chaos_snapshots",
        type_="unique",
    )
    op.drop_table("fetch_throttle_state")
    op.drop_constraint(
        op.f("ck_chaos_snapshots_capture_trigger"),
        "chaos_snapshots",
        type_="check",
    )
    op.drop_column("chaos_snapshots", "capture_policy_version")
    op.drop_column("chaos_snapshots", "capture_trigger")
