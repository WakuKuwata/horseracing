"""chaos snapshots and append-only readouts (Feature 084)

Revision ID: 0012_chaos_readout
Revises: 0011_model_purpose
Create Date: 2026-07-26

Persist the pre-race market snapshot used by the top-3 chaos readout and the values computed from
that immutable observation. This migration is additive only: no existing table, column, index, or
constraint is changed.

Only one active snapshot may exist per race. Readouts are append-only at the database boundary so
an already recorded display value cannot be rewritten after a race result becomes known.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_chaos_readout"
down_revision = "0011_model_purpose"
branch_labels = None
depends_on = None

_CREATE_REJECT_READOUT_UPDATE_FUNCTION = """
CREATE OR REPLACE FUNCTION reject_chaos_readout_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'chaos_readouts is append-only; UPDATE is not allowed';
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_REJECT_READOUT_UPDATE_TRIGGER = """
CREATE TRIGGER trg_chaos_readouts_reject_update
BEFORE UPDATE ON chaos_readouts
FOR EACH ROW EXECUTE FUNCTION reject_chaos_readout_update();
"""

_DROP_REJECT_READOUT_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_chaos_readouts_reject_update ON chaos_readouts;
"""

_DROP_REJECT_READOUT_UPDATE_FUNCTION = """
DROP FUNCTION IF EXISTS reject_chaos_readout_update();
"""


def upgrade() -> None:
    op.create_table(
        "chaos_snapshots",
        sa.Column(
            "chaos_snapshot_id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "race_id",
            sa.String(length=12),
            sa.ForeignKey("races.race_id"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("seconds_to_post", sa.Integer(), nullable=True),
        sa.Column("capture_strength", sa.Text(), nullable=False),
        sa.Column("field", JSONB(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chaos_snapshots_race_id_captured_at",
        "chaos_snapshots",
        ["race_id", sa.text("captured_at DESC")],
    )
    op.create_index(
        "ix_chaos_snapshots_race_id_status",
        "chaos_snapshots",
        ["race_id", "status"],
    )
    op.create_index(
        "uq_chaos_snapshots_active_race_id",
        "chaos_snapshots",
        ["race_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "chaos_readouts",
        sa.Column(
            "chaos_readout_id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "chaos_snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("chaos_snapshots.chaos_snapshot_id"),
            nullable=False,
        ),
        sa.Column("artifact_version", sa.Text(), nullable=False),
        sa.Column("artifact_digest", sa.Text(), nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("band_axis", sa.Text(), nullable=False),
        sa.Column("p_s_ge_20", sa.Numeric(), nullable=False),
        sa.Column("p_himo_are", sa.Numeric(), nullable=False),
        sa.Column("p_total_collapse", sa.Numeric(), nullable=False),
        sa.Column("raw_p_s_ge_20", sa.Numeric(), nullable=False),
        sa.Column("raw_p_himo_are", sa.Numeric(), nullable=False),
        sa.Column("raw_p_total_collapse", sa.Numeric(), nullable=False),
        sa.Column("expected_s", sa.Numeric(), nullable=False),
        sa.Column("structural_zeros", JSONB(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chaos_readouts_chaos_snapshot_id",
        "chaos_readouts",
        ["chaos_snapshot_id"],
    )
    op.create_index(
        "ix_chaos_readouts_computed_at",
        "chaos_readouts",
        [sa.text("computed_at DESC")],
    )

    op.execute(_CREATE_REJECT_READOUT_UPDATE_FUNCTION)
    op.execute(_CREATE_REJECT_READOUT_UPDATE_TRIGGER)


def downgrade() -> None:
    op.execute(_DROP_REJECT_READOUT_UPDATE_TRIGGER)
    op.execute(_DROP_REJECT_READOUT_UPDATE_FUNCTION)

    op.drop_index("ix_chaos_readouts_computed_at", table_name="chaos_readouts")
    op.drop_index("ix_chaos_readouts_chaos_snapshot_id", table_name="chaos_readouts")
    op.drop_table("chaos_readouts")

    op.drop_index("uq_chaos_snapshots_active_race_id", table_name="chaos_snapshots")
    op.drop_index("ix_chaos_snapshots_race_id_status", table_name="chaos_snapshots")
    op.drop_index("ix_chaos_snapshots_race_id_captured_at", table_name="chaos_snapshots")
    op.drop_table("chaos_snapshots")
