"""Feature 176: field-group provenance for the dual-source (netkeiba / JRA official) tables.

Two tables (migration 0023), because one row per (race, field_group) cannot say which HORSE's
value came from where — A's body weight updated from the official page would make B look official
too (codex plan review #1):

* ``race_horse_field_provenance`` — per (race, horse, group): entry_static / jockey / trainer /
  body_weight / entry_status.
* ``race_field_provenance`` — per (race, group): race_static / post_time / win_odds / place_odds /
  results / laps / win_payouts / exotic_payouts.

``source`` = the route whose value is CURRENTLY adopted (a same-value confirmation by the
higher-priority route moves it). ``observed_at`` = when the HTTP response was fetched, supplied by
the writer — never the DB write time. ``official_at`` is set only for the odds groups and must equal
``races.win_odds_official_at`` / ``races.place_odds_official_at`` (INV-P2). Written in the same
savepoint as the value (INV-P1). Never read by features / training / serving / betting / api
(INV-P4, leak-guard). No backfill: rows exist only for values written after 0023.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from ..base import Base
from ..constraints import JOB_SOURCE, RACE_FIELD_GROUP, RACE_HORSE_FIELD_GROUP
from ._mixins import TimestampMixin


class RaceHorseFieldProvenance(TimestampMixin, Base):
    __tablename__ = "race_horse_field_provenance"
    __table_args__ = (
        ForeignKeyConstraint(
            ["race_id", "horse_id"],
            ["race_horses.race_id", "race_horses.horse_id"],
            name=conv("fk_race_horse_field_provenance_race_horse"),
            ondelete="CASCADE",
        ),
        CheckConstraint(RACE_HORSE_FIELD_GROUP, name=conv("ck_race_horse_field_provenance_group")),
        CheckConstraint(JOB_SOURCE, name=conv("ck_race_horse_field_provenance_source")),
        Index("ix_race_horse_field_provenance_race", "race_id"),
    )

    race_id: Mapped[str] = mapped_column(Text, primary_key=True)
    horse_id: Mapped[str] = mapped_column(Text, primary_key=True)
    field_group: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_jobs.ingestion_job_id", ondelete="SET NULL")
    )


class RaceFieldProvenance(TimestampMixin, Base):
    __tablename__ = "race_field_provenance"
    __table_args__ = (
        CheckConstraint(RACE_FIELD_GROUP, name=conv("ck_race_field_provenance_group")),
        CheckConstraint(JOB_SOURCE, name=conv("ck_race_field_provenance_source")),
        CheckConstraint(
            "official_at IS NULL OR field_group IN ('win_odds', 'place_odds')",
            name=conv("ck_race_field_provenance_official_at_odds_only"),
        ),
    )

    race_id: Mapped[str] = mapped_column(
        Text, ForeignKey("races.race_id", ondelete="CASCADE"), primary_key=True
    )
    field_group: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    official_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_jobs.ingestion_job_id", ondelete="SET NULL")
    )
