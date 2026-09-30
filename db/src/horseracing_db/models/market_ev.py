"""Market-aware expected return (期待回収率) per started horse (Feature 137).

Maps onto migration 0018. The values come from a SEPARATE market-aware model that uses the current
win odds as an input, so the table sits outside the main prediction lineage: no FK to
``model_versions`` (not part of the single-active model contract) and none to ``horses`` (every row
is regenerable, so the entity re-key does not touch it). Nothing here may flow back into features,
training or calibration (leak guard: ``features/tests/unit/test_market_ev_leak_guard.py``).

Latest value only, no history (constitution V): a recompute replaces a race's rows. ``win_prob`` is
the raw binary output, not normalized within the race (constitution IV), so it is audit data and
never a displayed 1着率. No ``TimestampMixin``: ``computed_at`` is the single audit timestamp.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from ..base import Base


class MarketEvPrediction(Base):
    __tablename__ = "market_ev_predictions"
    __table_args__ = (
        # conv(): the names are already final (migration 0018 writes them verbatim with op.f()),
        # so the "ck" naming convention must not prefix them a second time.
        CheckConstraint(
            "win_prob > 0 AND win_prob < 1", name=conv("ck_market_ev_predictions_win_prob")
        ),
        CheckConstraint("odds_used >= 1.0", name=conv("ck_market_ev_predictions_odds_used")),
        CheckConstraint(
            "expected_return >= 0", name=conv("ck_market_ev_predictions_expected_return")
        ),
        Index("ix_market_ev_predictions_model_version_computed_at", "model_version", "computed_at"),
    )

    race_id: Mapped[str] = mapped_column(Text, ForeignKey("races.race_id"), primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    horse_id: Mapped[str] = mapped_column(Text, primary_key=True)
    horse_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: raw market-aware binary output (audit only — not a race-normalized 1着率)
    win_prob: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    odds_used: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    #: = win_prob × odds_used, stored at write time
    expected_return: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    #: race_horses.updated_at of the odds that were used
    odds_observed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: OBSERVED fact: no race_results row existed at compute time
    result_pending_at_compute: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: booster file actually applied (e.g. ``model_2026.txt``) and its sha256
    booster: Mapped[str] = mapped_column(Text, nullable=False)
    booster_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    logic_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: one value per compute run
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
