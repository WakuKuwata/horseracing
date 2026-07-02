"""race_predictions.explanation: per-horse score-contribution explanation (Feature 040)

Revision ID: 0008_prediction_explanation
Revises: 0007_race_laps
Create Date: 2026-07-02

Display-only prediction explanation. serving computes LightGBM pred_contrib (TreeSHAP) at
predict time and stores the top-K score contributions + audit fields as a JSONB payload on
each race_predictions row. The API is read-only and ML-free, so it cannot recompute this at
read time (only serving touches the booster) — hence it is persisted alongside the prediction
(constitution VI justification). Nullable: old runs / degenerate models stay NULL = 未提供.

The column holds a display-derived value, NEVER a model feature (constitution II leak
boundary). It does not change win/top2/top3 (INV-E2 byte-parity). No snapshot history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_prediction_explanation"
down_revision = "0007_race_laps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "race_predictions",
        sa.Column("explanation", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("race_predictions", "explanation")
