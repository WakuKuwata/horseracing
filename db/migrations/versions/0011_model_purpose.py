"""model_versions: human-readable purpose metadata (display_name / purpose) — Feature 057

Revision ID: 0011_model_purpose
Revises: 0010_raw_column_features
Create Date: 2026-07-06

Feature 057 (model switching) lets multiple purpose-labelled models coexist and be selected on the
race-detail prediction view. To tell models apart by intent (not just the technical ``model_version``
ID), add two nullable, human-readable columns:

- ``display_name`` — a short human name (e.g. "意思決定支援モデル"). Distinct from ``label_schema``
  (which is the label *scheme* 'win_top2_top3'), hence the name choice to avoid collision.
- ``purpose``      — a longer note on what the model is for.

Both are model *identity* metadata, NOT eval metrics (kept out of ``metrics_summary`` per the 021
transcription discipline). Nullable additions only; the PK (``model_version``) and every existing
column are untouched; existing rows stay NULL (populated later via the ``set-model-label`` CLI, same
"populate going forward" approach as 040 importance / 050 train_through). Never a model feature
(constitution II leak boundary — display-only).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_model_purpose"
down_revision = "0010_raw_column_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_versions", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("model_versions", sa.Column("purpose", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_versions", "purpose")
    op.drop_column("model_versions", "display_name")
