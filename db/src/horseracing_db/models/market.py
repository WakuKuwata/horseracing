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

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, Text, UniqueConstraint, Uuid, text
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
