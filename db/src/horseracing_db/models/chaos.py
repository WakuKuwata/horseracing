"""Top-3 chaos snapshot, readout, and fetch-throttle ORM models (Features 084/086).

These tables preserve the pre-race market observation and the derived values used for display.
Snapshot/readout models intentionally avoid ``TimestampMixin`` because their normative audit
schemas do not have the mixin's pair of timestamps. Fetch throttle rows are mutable operational
state rather than append-only audit records.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ChaosSnapshot(Base):
    """Frozen pre-race market observation used by a chaos readout."""

    __tablename__ = "chaos_snapshots"
    __table_args__ = (
        UniqueConstraint("race_id", name="uq_chaos_snapshots_race_id"),
    )

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
    capture_trigger: Mapped[str] = mapped_column(Text, nullable=False)
    capture_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
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


class FetchThrottleState(Base):
    """Mutable per-origin reservation and cooldown state for polite fetching."""

    __tablename__ = "fetch_throttle_state"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    next_allowed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    blocked_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    block_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
