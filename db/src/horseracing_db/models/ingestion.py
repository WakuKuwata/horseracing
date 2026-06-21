"""ID-mapping and ingestion-audit ORM models (US3): id_mappings, ingestion_jobs.

Maps onto migration 0002.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..constraints import (
    ID_ENTITY_TYPE,
    ID_SOURCE,
    JOB_SOURCE,
    JOB_STATUS,
    MAPPING_STATUS,
)
from ..enums import JobStatus, MappingStatus
from ._mixins import TimestampMixin


class IdMapping(TimestampMixin, Base):
    __tablename__ = "id_mappings"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "source", "source_id",
            name="uq_id_mappings_entity_source_sourceid",
        ),
        CheckConstraint(ID_ENTITY_TYPE, name="ck_id_mappings_entity_type"),
        CheckConstraint(ID_SOURCE, name="ck_id_mappings_source"),
        CheckConstraint(MAPPING_STATUS, name="ck_id_mappings_status"),
    )

    id_mapping_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_id: Mapped[str | None] = mapped_column(Text)
    mapping_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{MappingStatus.UNMAPPED}'")
    )
    conflict_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class IngestionJob(TimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(JOB_STATUS, name="ck_ingestion_jobs_status"),
        CheckConstraint(JOB_SOURCE, name="ck_ingestion_jobs_source"),
    )

    ingestion_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    source: Mapped[str | None] = mapped_column(Text)
    job_type: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    scope_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{JobStatus.QUEUED}'")
    )
    trace_id: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_retry: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    checkpoint: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
