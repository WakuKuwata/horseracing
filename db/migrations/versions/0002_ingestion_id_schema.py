"""ingestion / id-mapping schema (US3): id_mappings, ingestion_jobs

Revision ID: 0002_ingestion_id_schema
Revises: 0001_core_schema
Create Date: 2026-06-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from horseracing_db.constraints import (
    ID_ENTITY_TYPE,
    ID_SOURCE,
    JOB_SOURCE,
    JOB_STATUS,
    MAPPING_STATUS,
)
from horseracing_db.enums import JobStatus, MappingStatus
from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0002_ingestion_id_schema"
down_revision = "0001_core_schema"
branch_labels = None
depends_on = None

_TABLES = ("id_mappings", "ingestion_jobs")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "id_mappings",
        sa.Column("id_mapping_id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("canonical_id", sa.Text()),
        sa.Column("mapping_status", sa.Text(), nullable=False, server_default=MappingStatus.UNMAPPED),
        sa.Column("conflict_group_id", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text()),
        *_timestamps(),
        sa.UniqueConstraint("entity_type", "source", "source_id", name="uq_id_mappings_entity_source_sourceid"),
        sa.CheckConstraint(ID_ENTITY_TYPE, name="ck_id_mappings_entity_type"),
        sa.CheckConstraint(ID_SOURCE, name="ck_id_mappings_source"),
        sa.CheckConstraint(MAPPING_STATUS, name="ck_id_mappings_status"),
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("ingestion_job_id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.Text()),
        sa.Column("job_type", sa.Text()),
        sa.Column("scope", sa.Text()),
        sa.Column("scope_value", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default=JobStatus.QUEUED),
        sa.Column("trace_id", sa.Text()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retry", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("checkpoint", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        *_timestamps(),
        sa.CheckConstraint(JOB_STATUS, name="ck_ingestion_jobs_status"),
        sa.CheckConstraint(JOB_SOURCE, name="ck_ingestion_jobs_source"),
    )

    for table in _TABLES:
        op.execute(create_updated_at_trigger(table))


def downgrade() -> None:
    for table in _TABLES:
        op.execute(drop_updated_at_trigger(table))
    op.drop_table("ingestion_jobs")
    op.drop_table("id_mappings")
