"""Top-3 chaos snapshot and readout ORM models (Feature 084).

These tables preserve the pre-race market observation and the derived values used for display.
They intentionally define only ``created_at`` rather than using ``TimestampMixin`` because the
normative append-only schema has no ``updated_at`` column.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ChaosSnapshot(Base):
    """Frozen pre-race market observation used by a chaos readout."""

    __tablename__ = "chaos_snapshots"

    chaos_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    race_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("races.race_id"), nullable=False
    )
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    seconds_to_post: Mapped[int | None] = mapped_column(Integer)
    capture_strength: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ChaosReadout(Base):
    """Append-only values computed from one frozen chaos snapshot."""

    __tablename__ = "chaos_readouts"

    chaos_readout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    chaos_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("chaos_snapshots.chaos_snapshot_id"),
        nullable=False,
    )
    artifact_version: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_digest: Mapped[str] = mapped_column(Text, nullable=False)
    band: Mapped[str] = mapped_column(Text, nullable=False)
    band_axis: Mapped[str] = mapped_column(Text, nullable=False)
    p_s_ge_20: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    p_himo_are: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    p_total_collapse: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    raw_p_s_ge_20: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    raw_p_himo_are: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    raw_p_total_collapse: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    expected_s: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    structural_zeros: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
