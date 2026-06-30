"""race_laps: race-level sectional lap profile (Feature 034)

Revision ID: 0007_race_laps
Revises: 0006_stake_fraction
Create Date: 2026-06-30

§4 sectional data. The db.netkeiba race page carries a per-race ラップタイム profile (200m segment
times + テン3F/上がり3F split) that the core tables have no place for (race_results is per-horse;
races is pre-race entry data). A minimal NEW table holds it, RESULT-derived and kept separate from
the pre-race `races` table so the leak boundary stays explicit (constitution II/VI): lap data is
never a current-race feature — only past races' values are read as-of (Feature 035).

Single latest value per race + updated_at — NO snapshot history (constitution V, same policy as
race_horses.odds / exotic_odds). lap_times is a JSONB array of per-200m seconds.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from horseracing_db.constraints import JOB_SOURCE
from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0007_race_laps"
down_revision = "0006_stake_fraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "race_laps",
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), primary_key=True),
        sa.Column("lap_times", JSONB(), nullable=False),
        sa.Column("pace_first_3f", sa.Numeric(), nullable=True),
        sa.Column("pace_last_3f", sa.Numeric(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'netkeiba'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(JOB_SOURCE, name="ck_race_laps_source"),
    )
    op.execute(create_updated_at_trigger("race_laps"))


def downgrade() -> None:
    op.execute(drop_updated_at_trigger("race_laps"))
    op.drop_table("race_laps")
