"""raw-column ingest widening: first_3f / prize_money / owner / breeder / bloodline lines (055)

Revision ID: 0010_raw_column_features
Revises: 0009_diagnostic_runs
Create Date: 2026-07-03

The JRA-VAN raw CSV carries 73 columns but ingest named only ~35; Feature 055 widens ingest
with six pre-verified columns (specs/055-raw-column-features/research.md):

- race_results.first_3f  — テン3F (first 3-furlong seconds). Verified semantically: for 1200m
  (=6F) races  finish_time == first_3f + last_3f  held for 100.000% of ~30k races (2010/2018/
  2024). Result-derived → as-of features only, never the current race's value (constitution II).
- races.prize_money      — 1着賞金 (万円). Race-constant (verified: 0 non-constant races in
  2024) = pre-published race condition, NOT a result.
- horses.owner_name / breeder_name / sire_line / damsire_line — owner is last-write-wins
  (transfers are rare; same treatment as sire_name, 026 precedent); the rest are immutable.

Nullable additions only; no constraint/index changes; existing rows untouched (populated by
re-running the idempotent per-year ingest). Missing raw values stay NULL (Unknown ≠ 0,
constitution IV).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_raw_column_features"
down_revision = "0009_diagnostic_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("race_results", sa.Column("first_3f", sa.Numeric(), nullable=True))
    op.add_column("races", sa.Column("prize_money", sa.Integer(), nullable=True))
    op.add_column("horses", sa.Column("owner_name", sa.Text(), nullable=True))
    op.add_column("horses", sa.Column("breeder_name", sa.Text(), nullable=True))
    op.add_column("horses", sa.Column("sire_line", sa.Text(), nullable=True))
    op.add_column("horses", sa.Column("damsire_line", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("horses", "damsire_line")
    op.drop_column("horses", "sire_line")
    op.drop_column("horses", "breeder_name")
    op.drop_column("horses", "owner_name")
    op.drop_column("races", "prize_money")
    op.drop_column("race_results", "first_3f")
