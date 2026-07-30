"""Real exotic (combination-bet) dividend odds ORM model (Feature 012).

Maps onto migration 0005. ``exotic_odds`` holds the REAL exotic odds scraped from netkeiba, one
row per (race_id, bet_type, selection) with the SINGLE latest value + updated_at — NO snapshot
history (constitution V, same policy as ``race_horses.odds``). Pre-race scrape = morning odds;
post-result scrape = final dividend (overwrites — netkeiba is the sole source, no JRA-VAN final
odds to protect). ``selection`` is the same JSONB-safe canonical array as Feature 011's
``to_selection`` (ordered exacta/trifecta, ascending-sorted quinella/wide/trio, single place), so
recommendations / estimated odds join by exact selection. Exotic odds are MARKET data — never a
model feature (leak boundary, constitution II).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..constraints import COVERAGE_SCOPE, EXOTIC_BET_TYPE, JOB_SOURCE
from ..enums import CoverageScope, Source
from ._mixins import TimestampMixin


class ExoticOdds(TimestampMixin, Base):
    __tablename__ = "exotic_odds"
    __table_args__ = (
        UniqueConstraint(
            "race_id", "bet_type", "selection", name="uq_exotic_odds_race_bettype_selection"
        ),
        CheckConstraint(EXOTIC_BET_TYPE, name="ck_exotic_odds_bet_type"),
        CheckConstraint(COVERAGE_SCOPE, name="ck_exotic_odds_coverage_scope"),
        CheckConstraint(JOB_SOURCE, name="ck_exotic_odds_source"),  # source IN (jra_van, netkeiba)
    )

    exotic_odds_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), nullable=False)
    bet_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: JSONB-safe canonical array of horse_numbers (Feature 011 to_selection shape).
    selection: Mapped[list] = mapped_column(JSONB, nullable=False)
    #: latest dividend odds (pre-race morning odds, overwritten to final dividend post-result).
    odds: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    coverage_scope: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{CoverageScope.PARTIAL}'")
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{Source.NETKEIBA}'")
    )


class RaceLaps(TimestampMixin, Base):
    """Feature 034: race-level sectional lap profile (maps onto migration 0007). One row per race,
    SINGLE latest value + updated_at — NO snapshot history (constitution V). lap_times is a JSONB
    array of per-200m seconds; pace_first_3f/pace_last_3f are the race テン3F/上がり3F split.
    RESULT-derived — never a current-race model feature, only past races read as-of (II)."""

    __tablename__ = "race_laps"
    __table_args__ = (
        CheckConstraint(JOB_SOURCE, name="ck_race_laps_source"),
    )

    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), primary_key=True)
    #: per-200m segment times of the race (leader-based pace profile).
    lap_times: Mapped[list] = mapped_column(JSONB, nullable=False)
    pace_first_3f: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    pace_last_3f: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{Source.NETKEIBA}'")
    )


class ExoticQuote(TimestampMixin, Base):
    """Pre-race price grid for one (race, exotic bet type) — the LOSING combinations included.

    Distinct from :class:`ExoticOdds`, which holds the final DIVIDEND and therefore only ever
    covers the combination that came in. A dividend cannot drive selection (it is knowable only
    after the race); this grid can, which is what makes a Dr.Z-style "which combination is
    mispriced" question askable at all.

    The whole grid lives in one JSONB column rather than one row per combination: an 18-horse
    trifecta grid alone is 4,896 combinations, and every consumer reads a race's grid as a unit.
    """

    __tablename__ = "exotic_quotes"
    __table_args__ = (
        UniqueConstraint("race_id", "bet_type", name="uq_exotic_quotes_race_bettype"),
        CheckConstraint(EXOTIC_BET_TYPE, name="ck_exotic_quotes_bet_type"),
        CheckConstraint("n_combinations > 0", name="ck_exotic_quotes_n_combinations"),
    )

    exotic_quote_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    race_id: Mapped[str] = mapped_column(ForeignKey("races.race_id"), nullable=False)
    bet_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: {canonical selection key -> [odds_low, odds_high|null, popularity|null]}. Only ワイド has a
    #: real high end; point-priced types keep null there so "is this a range?" stays answerable.
    quotes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    n_combinations: Mapped[int] = mapped_column(Integer, nullable=False)
    #: source-declared effective time of THIS observation (provenance of the single latest value)
    official_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'netkeiba'"))
