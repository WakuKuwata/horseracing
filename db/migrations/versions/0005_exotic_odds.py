"""exotic_odds: real exotic (combination-bet) dividend odds (Feature 012)

Revision ID: 0005_exotic_odds
Revises: 0004_ingestion_job_counts
Create Date: 2026-06-24

First NEW table since the foundational schema/contract migrations (0001-0004); features 006-011
added no schema. Justified under constitution principle VI: existing tables have no place for real
exotic odds (race_horses.odds is WIN odds only), so a minimal new table is added up front rather
than retrofitted later. Single latest value per (race_id, bet_type, selection) + updated_at — NO
snapshot history (constitution V, same policy as race_horses.odds). UNIQUE(race_id, bet_type,
selection) B-tree gives exact-equality join to recommendations / estimated odds on the canonical
JSONB selection array.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from horseracing_db.constraints import COVERAGE_SCOPE, EXOTIC_BET_TYPE, JOB_SOURCE
from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0005_exotic_odds"
down_revision = "0004_ingestion_job_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exotic_odds",
        sa.Column("exotic_odds_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("bet_type", sa.Text(), nullable=False),
        sa.Column("selection", JSONB(), nullable=False),
        sa.Column("odds", sa.Numeric(), nullable=False),
        sa.Column("coverage_scope", sa.Text(), nullable=False, server_default=sa.text("'partial'")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'netkeiba'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(EXOTIC_BET_TYPE, name="ck_exotic_odds_bet_type"),
        sa.CheckConstraint(COVERAGE_SCOPE, name="ck_exotic_odds_coverage_scope"),
        sa.CheckConstraint(JOB_SOURCE, name="ck_exotic_odds_source"),
        sa.UniqueConstraint("race_id", "bet_type", "selection",
                            name="uq_exotic_odds_race_bettype_selection"),
    )
    # writer-independent updated_at (single latest value, constitution V)
    op.execute(create_updated_at_trigger("exotic_odds"))


def downgrade() -> None:
    op.execute(drop_updated_at_trigger("exotic_odds"))
    op.drop_table("exotic_odds")
