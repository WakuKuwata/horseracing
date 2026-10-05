"""official_win_payouts — the official 単勝 (win) payout per winning horse (Feature 139)

Revision ID: 0020_official_win_payouts
Revises: 0019_attention_picks
Create Date: 2026-10-04

A win bet is pari-mutuel: what is paid is the FINAL odds, published on the result page as the
単勝 payout (``Payout_Detail_Table`` / ``tr.Tansho``). Until now no table held it — the exotic
parser skipped the row, and ``race_horses.odds`` of netkeiba-era races kept the last PRE-RACE odds
(the live odds endpoint stops serving win odds once a race settles). Feature 138's prospective
check settles on this value (selection policy v2), so it needs a home of its own.

Deliberately NOT ``exotic_odds`` (plan D1): that table's readers — the 080 gate, the API's dividend
view and ``canonical_selection`` — all assume combination bets; adding 'win' there would reach
every one of them. A small single-purpose table keeps the blast radius closed.

* One row per (race, horse_number); a dead heat is one row per winning horse.
* ``payout_yen`` is per 100 yen staked; 元返し (100) is a legal value.
* Overwritable (not append-only): an official correction must be followed. The writer only
  rewrites a row when a value actually changed, and ``html_sha256`` names the page it came from
  (plan D10 — no history table; constitution V keeps a single latest value).
* ``observed_at`` is when the result page was fetched (for an archive repair: the archive file's
  timestamp), not when the row was written.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0020_official_win_payouts"
down_revision = "0019_attention_picks"
branch_labels = None
depends_on = None

# Literal, not imported: a migration is a historical record (0015/0017/0018 precedent). op.f()
# keeps the constraint names exactly as written instead of letting the metadata naming convention
# prefix them a second time.
_TABLE = "official_win_payouts"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("horse_number", sa.Integer(), nullable=False),
        sa.Column("payout_yen", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False,
                  server_default=sa.text("'netkeiba_result'")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("html_sha256", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("race_id", "horse_number", name=op.f("pk_official_win_payouts")),
        sa.CheckConstraint("horse_number >= 1",
                           name=op.f("ck_official_win_payouts_horse_number")),
        sa.CheckConstraint("payout_yen >= 100", name=op.f("ck_official_win_payouts_payout_yen")),
        sa.CheckConstraint("source IN ('netkeiba_result')",
                           name=op.f("ck_official_win_payouts_source")),
    )
    # writer-independent updated_at (0001/0005 pattern)
    op.execute(create_updated_at_trigger(_TABLE))


def downgrade() -> None:
    op.execute(drop_updated_at_trigger(_TABLE))
    op.drop_table(_TABLE)
