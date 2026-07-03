"""diagnostic_runs: persisted offline diagnostics for read-only display (Feature 054)

Revision ID: 0009_diagnostic_runs
Revises: 0008_prediction_explanation
Create Date: 2026-07-03

Heavy walk-forward diagnostics (047 segment-edge etc.) are computed OFFLINE via the training CLI
and persisted here so the read-only API / admin console only transcribe (021 discipline — never
recompute in-request). The API is ML-free and cannot run fold-retraining walk-forwards, hence
persistence is required (constitution VI justification — first new table since 012/exotic_odds
column-wise change 0008).

Append-only (no overwrite); readers take the latest row per kind (computed_at DESC). payload is
the diagnostic's own output verbatim. NEVER a model-feature input (constitution II — market q and
race results live inside this payload).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_diagnostic_runs"
down_revision = "0008_prediction_explanation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_runs",
        sa.Column("diagnostic_run_id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("logic_version", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_diagnostic_runs_kind_computed_at",
        "diagnostic_runs",
        ["kind", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_diagnostic_runs_kind_computed_at", table_name="diagnostic_runs")
    op.drop_table("diagnostic_runs")
