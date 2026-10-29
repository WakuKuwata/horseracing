"""Feature 174: the near-post (T−3 min) decision record — one frozen row per race.

Not an odds history (constitution V, 1.0.1 note): the quote served ~3 minutes before post and the
predictions computed from it at that moment, kept so the decision can be settled and reproduced
later. Append-only at the database boundary (triggers in migration 0022).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class DecisionQuote(Base):
    __tablename__ = "decision_quotes"
    __table_args__ = (
        UniqueConstraint("race_id", name="uq_decision_quotes_race_id"),
        CheckConstraint("seconds_to_post >= 0", name="ck_decision_quotes_seconds_to_post"),
        CheckConstraint("n >= 2", name="ck_decision_quotes_n"),
    )

    decision_quote_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    race_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("races.race_id"), nullable=False
    )
    captured_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    official_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    post_time: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seconds_to_post: Mapped[int] = mapped_column(Integer, nullable=False)
    capture_target: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    #: per horse: horse_id, horse_number, win_odds, popularity, place_low, place_high
    field: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    #: per horse: p_ens, ev_ens, p_single, ev_single, p_mix, ev_mix (mix null if unavailable)
    pred: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    quote_digest: Mapped[str] = mapped_column(Text, nullable=False)
    field_digest: Mapped[str] = mapped_column(Text, nullable=False)
    features_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    ensemble_version: Mapped[str] = mapped_column(Text, nullable=False)
    ensemble_manifest: Mapped[str] = mapped_column(Text, nullable=False)
    ensemble_manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    single_version: Mapped[str] = mapped_column(Text, nullable=False)
    single_booster: Mapped[str] = mapped_column(Text, nullable=False)
    single_booster_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    mix_version: Mapped[str | None] = mapped_column(Text)
    mix_status: Mapped[str] = mapped_column(Text, nullable=False)
    mix_coefficient_year: Mapped[int | None] = mapped_column(Integer)
    logic_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
