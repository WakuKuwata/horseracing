"""Feature 178 (migration 0025): ``jrdb_files`` — one row per JRDB file VERSION (content sha).

The unit of idempotency for the JRDB route (research D9, data-model §2): the same bytes are
recorded once and change nothing on re-ingest; a differing-content file of the same
(kind, data_date, variant) is ordered automatically only for 速報→正式 and 日次→バックナンバー,
otherwise it is ``held`` until an operator approves it. Partial success is per race
(``race_status``: race_id → {status, reason, applied_at}); the file is ``applied`` only when every
race is.

No member-only content lives here: ``source_url_sha256`` instead of the URL, and the decoded
text stays under ``artifacts/jrdb/`` (git-ignored). ``published_at`` is the HTTP Last-Modified
(the "利用可能になった時刻" of FR-010, reached from provenance via
``ingestion_jobs.summary.files``).

Exported from ``models/__init__`` (tasks T016 ⏸): the production worker imports that module on
every job, so this export reaches production only with migration 0025 in rollout step R2.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from ..base import Base
from ._mixins import TimestampMixin

#: every kind the member site delivers (decoded or merely recorded)
JRDB_FILE_KINDS: tuple[str, ...] = (
    "BAC",
    "KYI",
    "KYH",
    "KYG",
    "UKC",
    "KZA",
    "KSA",
    "CZA",
    "CSA",
    "TYB",
    "HJC",
    "SED",
    "SEC",
    "SRB",
    "SKB",
    "OZ",
    "OW",
    "OU",
    "OT",
    "OV",
    "CYB",
    "CHA",
    "KAB",
    "MZA",
    "MSA",
)
JRDB_FILE_VARIANTS: tuple[str, ...] = ("daily", "venue_e", "venue_w", "venue_l", "archive")
JRDB_FILE_STATUSES: tuple[str, ...] = (
    "fetched",
    "partially_applied",
    "applied",
    "superseded",
    "rejected",
    "held",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


JRDB_FILE_KIND_CHECK = _in_list("kind", JRDB_FILE_KINDS)
JRDB_FILE_VARIANT_CHECK = _in_list("variant", JRDB_FILE_VARIANTS)
JRDB_FILE_STATUS_CHECK = _in_list("status", JRDB_FILE_STATUSES)


class JrdbFile(TimestampMixin, Base):
    __tablename__ = "jrdb_files"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "data_date",
            "variant",
            "sha256",
            name=conv("uq_jrdb_files_kind_date_variant_sha"),
        ),
        CheckConstraint(JRDB_FILE_KIND_CHECK, name=conv("ck_jrdb_files_kind")),
        CheckConstraint(JRDB_FILE_VARIANT_CHECK, name=conv("ck_jrdb_files_variant")),
        CheckConstraint(JRDB_FILE_STATUS_CHECK, name=conv("ck_jrdb_files_status")),
        Index("ix_jrdb_files_kind_date", "kind", "data_date"),
        Index("ix_jrdb_files_status", "status"),
    )

    jrdb_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: the file name's yymmdd (master files: the Thursday/Monday the doc names)
    data_date: Mapped[datetime.date] = mapped_column(nullable=False)
    variant: Mapped[str] = mapped_column(Text, nullable=False)
    #: sha256 of the EXTRACTED text (not the archive)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: detected bytes per record incl. terminator; a mismatch with the layout → status=rejected
    record_length: Mapped[int] = mapped_column(Integer, nullable=False)
    #: the member URL is never stored in clear
    source_url_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'fetched'"))
    #: race_id → {"status": applied|failed|skipped, "reason": str|None, "applied_at": iso}
    race_status: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: the LAST job that processed this file (informational; provenance reaches published_at
    #: through ingestion_jobs.summary.files, data-model §3a)
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_jobs.ingestion_job_id", ondelete="SET NULL"), nullable=True
    )


__all__ = [
    "JRDB_FILE_KINDS",
    "JRDB_FILE_KIND_CHECK",
    "JRDB_FILE_STATUSES",
    "JRDB_FILE_STATUS_CHECK",
    "JRDB_FILE_VARIANTS",
    "JRDB_FILE_VARIANT_CHECK",
    "JrdbFile",
]
