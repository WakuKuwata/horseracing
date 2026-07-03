"""Core race-data ORM models (US1/US2): races, horses, jockeys, trainers,
race_horses, race_results.

These map onto the tables created by migration 0001. data-model.md is the source
of truth for columns and constraints.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Interval,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..constraints import (
    ENTRY_STATUS,
    FINISH_ORDER_WHEN_FINISHED,
    RACE_ID_FORMAT,
    RACE_NUMBER_RANGE,
    RESULT_STATUS,
)
from ..enums import EntryStatus, ResultStatus
from ._mixins import TimestampMixin


class Race(TimestampMixin, Base):
    __tablename__ = "races"
    __table_args__ = (
        CheckConstraint(RACE_ID_FORMAT, name="ck_races_race_id_format"),
        CheckConstraint(RACE_NUMBER_RANGE, name="ck_races_race_number_range"),
    )

    race_id: Mapped[str] = mapped_column(Text, primary_key=True)
    race_name: Mapped[str | None] = mapped_column(Text)
    race_name_short: Mapped[str | None] = mapped_column(Text)
    venue_code: Mapped[str | None] = mapped_column(Text)
    distance: Mapped[int | None] = mapped_column(Integer)
    track_type: Mapped[str | None] = mapped_column(Text)
    race_status: Mapped[str | None] = mapped_column(Text)
    race_date: Mapped[datetime.date | None] = mapped_column(Date)
    race_number: Mapped[int | None] = mapped_column(Integer)
    grade: Mapped[str | None] = mapped_column(Text)
    race_class: Mapped[str | None] = mapped_column(Text)
    weather: Mapped[str | None] = mapped_column(Text)
    going: Mapped[str | None] = mapped_column(Text)
    post_time: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Feature 055: 1着賞金 (万円) — race-constant, pre-published race condition (not a result)
    prize_money: Mapped[int | None] = mapped_column(Integer)


class Horse(TimestampMixin, Base):
    __tablename__ = "horses"

    horse_id: Mapped[str] = mapped_column(Text, primary_key=True)
    horse_name: Mapped[str | None] = mapped_column(Text)
    sex: Mapped[str | None] = mapped_column(Text)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    sire_id: Mapped[str | None] = mapped_column(Text)
    dam_id: Mapped[str | None] = mapped_column(Text)
    damsire_id: Mapped[str | None] = mapped_column(Text)
    sire_name: Mapped[str | None] = mapped_column(Text)
    dam_name: Mapped[str | None] = mapped_column(Text)
    damsire_name: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(Text)
    #: Feature 055 — owner is last-write-wins (transfers rare, 026 sire_name precedent);
    #: breeder / bloodline lines are immutable horse attributes.
    owner_name: Mapped[str | None] = mapped_column(Text)
    breeder_name: Mapped[str | None] = mapped_column(Text)
    sire_line: Mapped[str | None] = mapped_column(Text)
    damsire_line: Mapped[str | None] = mapped_column(Text)


class Jockey(TimestampMixin, Base):
    __tablename__ = "jockeys"

    jockey_id: Mapped[str] = mapped_column(Text, primary_key=True)
    jockey_name: Mapped[str | None] = mapped_column(Text)


class Trainer(TimestampMixin, Base):
    __tablename__ = "trainers"

    trainer_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trainer_name: Mapped[str | None] = mapped_column(Text)


class RaceHorse(TimestampMixin, Base):
    __tablename__ = "race_horses"
    __table_args__ = (
        CheckConstraint(ENTRY_STATUS, name="ck_race_horses_entry_status"),
    )

    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), primary_key=True)
    horse_id: Mapped[str] = mapped_column(ForeignKey("horses.horse_id"), primary_key=True)
    sex: Mapped[str | None] = mapped_column(Text)
    age: Mapped[int | None] = mapped_column(Integer)
    frame: Mapped[int | None] = mapped_column(Integer)
    horse_number: Mapped[int | None] = mapped_column(Integer)
    jockey_id: Mapped[str | None] = mapped_column(ForeignKey("jockeys.jockey_id"))
    trainer_id: Mapped[str | None] = mapped_column(ForeignKey("trainers.trainer_id"))
    weight: Mapped[int | None] = mapped_column(Integer)
    weight_diff: Mapped[int | None] = mapped_column(Integer)
    odds: Mapped[Decimal | None] = mapped_column(Numeric)
    popularity: Mapped[int | None] = mapped_column(Integer)
    running_style: Mapped[str | None] = mapped_column(Text)
    jockey_weight: Mapped[Decimal | None] = mapped_column(Numeric)
    entry_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{EntryStatus.STARTED}'")
    )


class RaceResult(TimestampMixin, Base):
    __tablename__ = "race_results"
    __table_args__ = (
        CheckConstraint(RESULT_STATUS, name="ck_race_results_result_status"),
        CheckConstraint(
            FINISH_ORDER_WHEN_FINISHED,
            name="ck_race_results_finish_order_when_finished",
        ),
    )

    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), primary_key=True)
    horse_id: Mapped[str] = mapped_column(ForeignKey("horses.horse_id"), primary_key=True)
    finish_order: Mapped[int | None] = mapped_column(Integer)
    finish_time: Mapped[datetime.timedelta | None] = mapped_column(Interval)
    finish_time_diff: Mapped[datetime.timedelta | None] = mapped_column(Interval)
    corner_orders: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    last_3f: Mapped[Decimal | None] = mapped_column(Numeric)
    #: Feature 055: テン3F (first 3F seconds). Result-derived → as-of features only (II).
    first_3f: Mapped[Decimal | None] = mapped_column(Numeric)
    result_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{ResultStatus.FINISHED}'")
    )
