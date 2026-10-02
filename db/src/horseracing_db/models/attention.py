"""注目条件 (attention conditions) — prospective record of judged horses (Feature 138).

Maps onto migration 0019. Three append-only tables (UPDATE / DELETE / TRUNCATE are rejected by
database triggers — 106 precedent), all outside the main prediction lineage and never read by
features, training of the win model or calibration (leak guard).

* ``AttentionRaceScan`` — one row per race (and rule-set version) at its FIRST market-ev ensemble
  computation. Whether a run could insert this row decides whether that run may create picks
  (plan D16): later computations never add picks, even for a race that had none.
* ``AttentionPick`` — a horse that matched a rule at that first computation, frozen with the values
  used (``kind='pick'``), plus ``kind='void'`` rows appended when the picked horse is later
  scratched. One pick per (race, horse, rule, rule-set version) for life (086 precedent).
* ``AttentionCheckpoint`` — the recorded 300/600-point decision of a rule; the stage shown on
  screen follows these records so late result corrections cannot flip it (plan D18).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from ..base import Base

ATTENTION_RULE_IDS = ("S1", "S2", "S3", "S4", "S5")
ATTENTION_PICK_KINDS = ("pick", "void")
ATTENTION_VOID_REASONS = ("scratched",)
ATTENTION_DECISIONS = ("passed", "failed", "continue", "undecided")
ATTENTION_CHECKPOINTS = (300, 600)


def _in(column: str, values) -> str:
    """``col IN (...)`` with SQL literals (ints bare, strings single-quoted via repr)."""
    return f"{column} IN ({', '.join(str(v) if isinstance(v, int) else repr(v) for v in values)})"


class AttentionRaceScan(Base):
    __tablename__ = "attention_race_scans"
    __table_args__ = (
        CheckConstraint("n_picks >= 0", name=conv("ck_attention_race_scans_n_picks")),
    )

    race_id: Mapped[str] = mapped_column(Text, ForeignKey("races.race_id"), primary_key=True)
    rule_set_version: Mapped[str] = mapped_column(Text, primary_key=True)
    #: = the run_id of the market-ev rows written by the same run
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    post_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_pending_at_compute: Mapped[bool] = mapped_column(Boolean, nullable=False)
    field_digest: Mapped[str] = mapped_column(Text, nullable=False)
    #: kind='pick' rows (horse × rule) written by this run — 0 is recorded too
    n_picks: Mapped[int] = mapped_column(Integer, nullable=False)


class AttentionPick(Base):
    __tablename__ = "attention_picks"
    __table_args__ = (
        CheckConstraint(
            _in("rule_id", ATTENTION_RULE_IDS), name=conv("ck_attention_picks_rule_id")
        ),
        CheckConstraint(_in("kind", ATTENTION_PICK_KINDS), name=conv("ck_attention_picks_kind")),
        CheckConstraint(
            "(kind = 'void') = (void_reason IS NOT NULL)",
            name=conv("ck_attention_picks_void_reason_present"),
        ),
        CheckConstraint(
            "void_reason IS NULL OR " + _in("void_reason", ATTENTION_VOID_REASONS),
            name=conv("ck_attention_picks_void_reason"),
        ),
        CheckConstraint(
            "(kind = 'void') = (voids_pick_id IS NOT NULL)",
            name=conv("ck_attention_picks_void_target"),
        ),
        CheckConstraint(
            "kind <> 'pick' OR (ens_expected_return IS NOT NULL AND odds_used >= 1.0 "
            "AND odds_observed_at IS NOT NULL AND horse_number IS NOT NULL)",
            name=conv("ck_attention_picks_pick_values"),
        ),
        Index(
            "uq_attention_picks_pick",
            "race_id",
            "horse_id",
            "rule_id",
            "rule_set_version",
            unique=True,
            postgresql_where=text("kind = 'pick'"),
        ),
        Index(
            "uq_attention_picks_void",
            "voids_pick_id",
            unique=True,
            postgresql_where=text("kind = 'void'"),
        ),
        Index("ix_attention_picks_race", "race_id"),
        Index("ix_attention_picks_rule_computed", "rule_id", "computed_at"),
    )

    pick_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    race_id: Mapped[str] = mapped_column(Text, ForeignKey("races.race_id"), nullable=False)
    horse_id: Mapped[str] = mapped_column(Text, nullable=False)
    horse_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    rule_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voids_pick_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("attention_picks.pick_id"), nullable=True
    )
    ens_expected_return: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    single_expected_return: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    odds_used: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    odds_observed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    days_since_last: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    #: races.post_time at compute time (the tally uses this stored value)
    post_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: post_time − computed_at in seconds (diagnostic)
    seconds_to_post: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: no race_results row at compute time AND (post_time unknown OR computed_at < post_time)
    result_pending_at_compute: Mapped[bool] = mapped_column(Boolean, nullable=False)
    field_digest: Mapped[str] = mapped_column(Text, nullable=False)
    ensemble_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    single_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    logic_version: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    selection_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AttentionCheckpoint(Base):
    __tablename__ = "attention_checkpoints"
    __table_args__ = (
        CheckConstraint(
            _in("rule_id", ATTENTION_RULE_IDS), name=conv("ck_attention_checkpoints_rule_id")
        ),
        CheckConstraint(
            _in("checkpoint", ATTENTION_CHECKPOINTS),
            name=conv("ck_attention_checkpoints_checkpoint"),
        ),
        CheckConstraint(
            _in("decision", ATTENTION_DECISIONS), name=conv("ck_attention_checkpoints_decision")
        ),
        CheckConstraint(
            "(checkpoint = 300 AND decision <> 'undecided') "
            "OR (checkpoint = 600 AND decision <> 'continue')",
            name=conv("ck_attention_checkpoints_decision_by_checkpoint"),
        ),
        CheckConstraint("n_counted = checkpoint", name=conv("ck_attention_checkpoints_n_counted")),
        UniqueConstraint(
            "rule_id",
            "checkpoint",
            "selection_policy_version",
            "rule_set_version",
            name="uq_attention_checkpoints_decision",
        ),
        Index("ix_attention_checkpoints_rule", "rule_id"),
    )

    checkpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    rule_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    n_counted: Mapped[int] = mapped_column(Integer, nullable=False)
    n_hits: Mapped[int] = mapped_column(Integer, nullable=False)
    #: settled at the judged odds (odds_used × 100 yen)
    roi_frozen: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ci_low: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    ci_high: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    bootstrap: Mapped[dict] = mapped_column(JSONB, nullable=False)
    counted_pick_ids_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    last_pick_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("attention_picks.pick_id"), nullable=False
    )
    #: decision time − 3 days: only picks that started before this were material
    settlement_cutoff: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    prospective_start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    skipped_pending_before_last: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
