"""Prediction / recommendation minimal-contract ORM models (US4).

Maps onto migration 0003. Combination-bet probability detail and estimated-odds
conversion are P0-deferred (FR-023); ``selection`` is jsonb to absorb per-bet-type
structure without a breaking change.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..constraints import ADOPTION_STATUS, BET_TYPE, PROB_MONOTONIC
from ..enums import AdoptionStatus
from ._mixins import TimestampMixin


class ModelVersion(TimestampMixin, Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint(ADOPTION_STATUS, name="ck_model_versions_adoption_status"),
    )

    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    model_family: Mapped[str | None] = mapped_column(Text)
    feature_version: Mapped[str | None] = mapped_column(Text)
    label_schema: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'win_top2_top3'")
    )
    adoption_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{AdoptionStatus.CANDIDATE}'")
    )
    metrics_summary: Mapped[dict | None] = mapped_column(JSONB)
    weights_uri: Mapped[str | None] = mapped_column(Text)
    calibrator_uri: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class PredictionRun(TimestampMixin, Base):
    __tablename__ = "prediction_runs"

    prediction_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), nullable=False)
    model_version: Mapped[str] = mapped_column(
        ForeignKey("model_versions.model_version"), nullable=False
    )
    logic_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RacePrediction(TimestampMixin, Base):
    __tablename__ = "race_predictions"
    __table_args__ = (
        CheckConstraint(PROB_MONOTONIC, name="ck_race_predictions_prob_monotonic"),
    )

    prediction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prediction_runs.prediction_run_id"), primary_key=True
    )
    horse_id: Mapped[str] = mapped_column(ForeignKey("horses.horse_id"), primary_key=True)
    win_prob: Mapped[Decimal | None] = mapped_column(Numeric)
    top2_prob: Mapped[Decimal | None] = mapped_column(Numeric)
    top3_prob: Mapped[Decimal | None] = mapped_column(Numeric)
    # Feature 040: display-only score-contribution explanation (TreeSHAP top-K + audit).
    # NULL = 未提供 (old runs / degenerate model). NEVER a model feature (leak boundary II).
    explanation: Mapped[dict | None] = mapped_column(JSONB)


class FeatureSnapshot(TimestampMixin, Base):
    __tablename__ = "feature_snapshots"

    prediction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prediction_runs.prediction_run_id"), primary_key=True
    )
    horse_id: Mapped[str] = mapped_column(ForeignKey("horses.horse_id"), primary_key=True)
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint(BET_TYPE, name="ck_recommendations_bet_type"),
    )

    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    prediction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prediction_runs.prediction_run_id"), nullable=False
    )
    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), nullable=False)
    bet_type: Mapped[str] = mapped_column(Text, nullable=False)
    selection: Mapped[dict] = mapped_column(JSONB, nullable=False)
    market_odds_used: Mapped[Decimal | None] = mapped_column(Numeric)
    estimated_market_odds_used: Mapped[Decimal | None] = mapped_column(Numeric)
    is_estimated_odds: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    pseudo_odds: Mapped[Decimal | None] = mapped_column(Numeric)
    pseudo_roi: Mapped[Decimal | None] = mapped_column(Numeric)
    # Kelly effective bet-size fraction (Feature 016); NULL for flat (011/012) rows.
    stake_fraction: Mapped[Decimal | None] = mapped_column(Numeric)
    logic_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DiagnosticRun(TimestampMixin, Base):
    """Feature 054: persisted offline diagnostics (e.g. 047 segment-edge) for read-only display.

    Heavy walk-forward diagnostics are computed OFFLINE (CLI) and persisted here so the read-only
    API / admin console only ever transcribe (021 discipline — never recompute in-request).
    Append-only; readers take the latest row per kind (computed_at DESC). ``payload`` is the
    diagnostic's own output verbatim (no derived metrics added at read time, constitution III).
    NEVER a model-feature input (constitution II leak boundary — market q / results live inside).
    """

    __tablename__ = "diagnostic_runs"

    diagnostic_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. 'segment_edge'
    date_from: Mapped[datetime.date | None] = mapped_column(Date)  # evaluation window
    date_to: Mapped[datetime.date | None] = mapped_column(Date)
    logic_version: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
