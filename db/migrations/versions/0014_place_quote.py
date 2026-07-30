"""place (複勝) market quote on race_horses + its observation provenance on races

Revision ID: 0014_place_quote
Revises: 0013_capture_provenance
Create Date: 2026-07-29

netkeiba's win-odds JSON (already fetched daily, 0 extra requests) also carries 複勝 odds as a
RANGE per horse and a source-declared observation time, both of which were parsed away. Store them
so the 複勝 pool — the only JRA market with the same 20% takeout as win — becomes measurable
against real prices instead of Harville-estimated ones.

Deliberately NOT reusing `exotic_odds`: that table is the single-latest value per
(race_id, bet_type, selection) and now holds REAL DIVIDENDS. Writing pre-race place quotes into
the same rows would overwrite settled dividends (and a range cannot fit its single `odds` column).
A market quote and a final dividend are different facts; constitution V forbids keeping a HISTORY
of each fact, not keeping the facts apart.

Additive and nullable only: no backfill, no default, existing rows keep NULL. The feature loader
selects an explicit column list, so `source_fingerprint` and every materialized artifact are
unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_place_quote"
down_revision = "0013_capture_provenance"
branch_labels = None
depends_on = None

# Kept literal (not imported from horseracing_db.constraints): a migration is a historical record,
# so replaying it must not depend on the CURRENT shape of application code.
_PLACE_ODDS_RANGE = (
    "(place_odds_low IS NULL AND place_odds_high IS NULL) OR "
    "(place_odds_low > 0 AND place_odds_high > 0 AND place_odds_low <= place_odds_high)"
)


def upgrade() -> None:
    op.add_column("race_horses", sa.Column("place_odds_low", sa.Numeric(), nullable=True))
    op.add_column("race_horses", sa.Column("place_odds_high", sa.Numeric(), nullable=True))
    op.add_column("race_horses", sa.Column("place_popularity", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_race_horses_place_odds_range", "race_horses", _PLACE_ODDS_RANGE)

    op.add_column(
        "races",
        sa.Column("place_odds_official_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "races",
        sa.Column("place_odds_observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("races", "place_odds_observed_at")
    op.drop_column("races", "place_odds_official_at")
    op.drop_constraint("ck_race_horses_place_odds_range", "race_horses", type_="check")
    op.drop_column("race_horses", "place_popularity")
    op.drop_column("race_horses", "place_odds_high")
    op.drop_column("race_horses", "place_odds_low")
