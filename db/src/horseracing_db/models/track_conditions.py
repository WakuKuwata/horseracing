"""Feature 176 (US4, migration 0024): JRA official track-condition readings.

(含水率・クッション値・馬場状態・天候)

One row per (venue_code, measured_at, item). The item code carries the measured object
(``moisture_turf_goal`` …) so the four simultaneous moisture readings do not collide (research
D10). ``time_basis`` says whether ``measured_at`` is the fragment's own measurement time
(``measured``, with year correction) or the fetch time (``fetched`` — accessJ's going/weather
have no measurement time; the cushion time is NOT reused for them). ``fetched_at`` is the latest
fetch that ADOPTED this value (unchanged on a same-value re-fetch). ``published_at`` is NULL in
v1 (the fragments do not publish it).

Display / research only. Never read by features / training / serving / betting / api (FR-021,
static + dynamic leak-guard); not part of ``source_fingerprint``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Numeric, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from ..base import Base
from ..constraints import (
    TRACK_CONDITION_ITEM,
    TRACK_CONDITION_TIME_BASIS,
    TRACK_CONDITION_VENUE,
)
from ..enums import Source
from ._mixins import TimestampMixin


class JraTrackCondition(TimestampMixin, Base):
    __tablename__ = "jra_track_conditions"
    __table_args__ = (
        CheckConstraint(TRACK_CONDITION_VENUE, name=conv("ck_jra_track_conditions_venue")),
        CheckConstraint(TRACK_CONDITION_ITEM, name=conv("ck_jra_track_conditions_item")),
        CheckConstraint(
            TRACK_CONDITION_TIME_BASIS, name=conv("ck_jra_track_conditions_time_basis")
        ),
        CheckConstraint(
            "measured_at <= fetched_at", name=conv("ck_jra_track_conditions_not_future")
        ),
    )

    venue_code: Mapped[str] = mapped_column(Text, primary_key=True)
    measured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    item: Mapped[str] = mapped_column(Text, primary_key=True)
    time_basis: Mapped[str] = mapped_column(Text, nullable=False)
    value_num: Mapped[Decimal | None] = mapped_column(Numeric)
    value_text: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{Source.JRA_OFFICIAL}'")
    )
