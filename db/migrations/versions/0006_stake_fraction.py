"""recommendations.stake_fraction: Kelly effective bet-size fraction (Feature 016)

Revision ID: 0006_stake_fraction
Revises: 0005_exotic_odds
Create Date: 2026-06-26

Kelly staking (016) optimizes HOW MUCH to bet. The 011/012 flat-stake path stored only per-unit
pseudo metrics (pseudo_odds=1/P_model, pseudo_roi=EV-1) — there was no column for a bet-size
fraction. Kelly's core output is the per-row effective fraction (λ·cap·allocation applied), so a
single nullable column is added. flat (011/012) rows keep it NULL (backward compatible). The full
Kelly config (λ_real/λ_est, cap_bet, cap_total, o_min, bankroll, allocation method, odds source,
009/010 versions) is encoded in logic_version, so absolute stake = stake_fraction × bankroll is
reproducible (constitution V). Justified under principle VI: existing nullable columns carry
distinct meanings, so overloading them would harm auditability — a minimal nullable column is the
smallest backward-compatible change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_stake_fraction"
down_revision = "0005_exotic_odds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("stake_fraction", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendations", "stake_fraction")
