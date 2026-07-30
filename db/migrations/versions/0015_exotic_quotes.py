"""pre-race exotic price grid (exotic_quotes)

Revision ID: 0015_exotic_quotes
Revises: 0014_place_quote
Create Date: 2026-07-29

Every ROI measurement so far priced combination bets from the WIN pool, because the exotic pools'
own prices were never stored — `exotic_odds` holds DIVIDENDS, which exist only for the combination
that came in. That made the one question Hausch–Ziemba–Rubinstein actually answered ("which
individual combination is mispriced?") impossible to ask: you cannot select on a price you only
learn after the race.

netkeiba's odds API returns the FULL grid, losing combinations included, at `type=4..8`
(馬連 / ワイド / 馬単 / 三連複 / 三連単). This table stores it.

Shape: one row per (race_id, bet_type) holding the whole grid as JSONB, not one row per
combination. A single 18-horse trifecta grid is 4,896 combinations; per-row storage would be
~21M rows/year for that bet type alone, and every consumer reads a whole race's grid at once
anyway.

`quotes` maps a canonical selection key ("1-2", "1-2-3" — ordered types keep finishing order,
unordered types sort ascending, matching `horseracing_db.selection.canonical_selection`) to
``[odds_low, odds_high_or_null, popularity_or_null]``. Only ワイド carries a real high end; the
point-priced types store null there rather than repeating the low value, so "is this a range?"
stays answerable from the data.

Single latest value per (race, bet_type) — constitution V. `official_at` is the source-declared
effective time of that observation and `observed_at` is when we fetched it; both are provenance
of the one stored value, not a history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_exotic_quotes"
down_revision = "0014_place_quote"
branch_labels = None
depends_on = None

# Literal, not imported from horseracing_db.enums: a migration is a historical record and must
# replay independently of the current shape of application code.
_BET_TYPES = ("place", "quinella", "exacta", "wide", "trio", "trifecta")


def upgrade() -> None:
    op.create_table(
        "exotic_quotes",
        sa.Column("exotic_quote_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("bet_type", sa.Text(), nullable=False),
        sa.Column("quotes", postgresql.JSONB(), nullable=False),
        sa.Column("n_combinations", sa.Integer(), nullable=False),
        sa.Column("official_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'netkeiba'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("race_id", "bet_type", name="uq_exotic_quotes_race_bettype"),
        sa.CheckConstraint(
            "bet_type IN (" + ", ".join(f"'{b}'" for b in _BET_TYPES) + ")",
            name="ck_exotic_quotes_bet_type",
        ),
        sa.CheckConstraint("n_combinations > 0", name="ck_exotic_quotes_n_combinations"),
    )
    op.create_index("ix_exotic_quotes_race", "exotic_quotes", ["race_id"])


def downgrade() -> None:
    op.drop_index("ix_exotic_quotes_race", table_name="exotic_quotes")
    op.drop_table("exotic_quotes")
