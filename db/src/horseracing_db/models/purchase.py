"""Purchase records — the user's ACTUAL betting actions, append-only (Feature 106).

Maps onto migration 0017. The presented slip is FROZEN into the record (research D2: stake
amounts derive from a budget that exists only in the front-end's localStorage, so the server
cannot reproduce the presentation later). Append-only is enforced by DB triggers, not here —
this class intentionally has no update-shaped helpers.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

#: record kinds (migration 0017 CHECK). Skips are THREE distinct kinds (codex Q2): only a
#: verified-empty presentation (`no_recommendation`) legitimately zeroes the policy line;
#: `presentation_unavailable` is UNKNOWN, not zero.
PURCHASE_KINDS: tuple[str, ...] = (
    "as_presented", "modified", "skipped_presented", "no_recommendation",
    "presentation_unavailable", "freeform", "correction", "void",
)


class PurchaseRecord(Base):
    __tablename__ = "purchase_records"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in PURCHASE_KINDS)),
            name="ck_purchase_records_kind",
        ),
        CheckConstraint(
            "(kind IN ('correction', 'void')) = (corrects_record_id IS NOT NULL)",
            name="ck_purchase_records_correction_target",
        ),
        UniqueConstraint("client_request_id", name="uq_purchase_records_client_request"),
    )

    purchase_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: actual bets [{bet_type, selection, amount_yen, odds_used?}] — [] for skips/void
    bets: Mapped[list] = mapped_column(JSONB, nullable=False)
    #: the slip as rendered (frozen; NULL for correction/void/freeform/presentation_unavailable)
    presented_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    prediction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prediction_runs.prediction_run_id"), nullable=True
    )
    corrects_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_records.purchase_record_id"), nullable=True
    )
    #: OBSERVED fact: no race_results row existed at record time (NOT a "pre-race" claim — D5)
    result_pending_at_record: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pending_basis_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    client_request_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
