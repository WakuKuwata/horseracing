"""jra_track_conditions — official 含水率・クッション値・馬場状態・天候 (Feature 176, US4 / Phase 3)

Revision ID: 0024_jra_track_conditions
Revises: 0023_jra_official_source
Create Date: 2026-11-02

One row per (venue_code, measured_at, item); the item code names the measured object so the four
simultaneous moisture readings do not collide. ``time_basis`` distinguishes a measurement time
(fragments) from a fetch time (accessJ going/weather, which carry none). Storage only — not a model
feature in this feature (FR-021; static + dynamic leak-guard in features/tests).

Downgrade refuses once rows exist (the data has no other home).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0024_jra_track_conditions"
down_revision = "0023_jra_official_source"
branch_labels = None
depends_on = None

_TABLE = "jra_track_conditions"
_ITEMS = (
    "('moisture_turf_goal', 'moisture_turf_corner4', 'moisture_dirt_goal', "
    "'moisture_dirt_corner4', 'cushion_turf', 'going_turf', 'going_dirt', 'weather')"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        _TABLE,
        sa.Column("venue_code", sa.Text(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item", sa.Text(), nullable=False),
        sa.Column("time_basis", sa.Text(), nullable=False),
        sa.Column("value_num", sa.Numeric(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_sha256", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'jra_official'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("venue_code", "measured_at", "item", name=op.f(f"pk_{_TABLE}")),
        sa.CheckConstraint("venue_code ~ '^[0-9]{2}$'", name=op.f("ck_jra_track_conditions_venue")),
        sa.CheckConstraint(f"item IN {_ITEMS}", name=op.f("ck_jra_track_conditions_item")),
        sa.CheckConstraint(
            "time_basis IN ('measured', 'fetched')", name=op.f("ck_jra_track_conditions_time_basis")
        ),
        sa.CheckConstraint(
            "measured_at <= fetched_at", name=op.f("ck_jra_track_conditions_not_future")
        ),
    )
    op.execute(create_updated_at_trigger(_TABLE))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")).first() is not None:
        raise RuntimeError(
            f"downgrade refused: {_TABLE} has rows and no other table holds them. "
            "Stop jra-baba instead of dropping the table."
        )
    op.execute(drop_updated_at_trigger(_TABLE))
    op.drop_table(_TABLE)
