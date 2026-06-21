"""ingestion_jobs audit counts + skipped status (non-breaking)

Revision ID: 0004_ingestion_job_counts
Revises: 0003_prediction_contract
Create Date: 2026-06-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from horseracing_db.constraints import JOB_STATUS

revision = "0004_ingestion_job_counts"
down_revision = "0003_prediction_contract"
branch_labels = None
depends_on = None

# Status set before `skipped` was added (for downgrade).
_OLD_JOB_STATUS = "status IN ('queued', 'running', 'succeeded', 'failed', 'partial')"


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("processed_rows", sa.Integer()))
    op.add_column("ingestion_jobs", sa.Column("skipped_rows", sa.Integer()))
    op.add_column("ingestion_jobs", sa.Column("error_count", sa.Integer()))
    op.add_column("ingestion_jobs", sa.Column("summary", JSONB()))

    # Replace the status CHECK to allow the new `skipped` value.
    op.drop_constraint("ck_ingestion_jobs_status", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_ingestion_jobs_status", "ingestion_jobs", JOB_STATUS)


def downgrade() -> None:
    op.drop_constraint("ck_ingestion_jobs_status", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_ingestion_jobs_status", "ingestion_jobs", _OLD_JOB_STATUS)

    op.drop_column("ingestion_jobs", "summary")
    op.drop_column("ingestion_jobs", "error_count")
    op.drop_column("ingestion_jobs", "skipped_rows")
    op.drop_column("ingestion_jobs", "processed_rows")
