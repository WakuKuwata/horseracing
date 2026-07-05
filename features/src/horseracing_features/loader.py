"""Load races / race_horses / race_results into pandas (2007+ pool).

Loads the full pool from the 2007 ingest boundary up to ``end_date`` so that
past-performance history is complete even for target races early in a window.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import pandas as pd
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult
from horseracing_db.validation import INGEST_SCOPE_START
from sqlalchemy import select
from sqlalchemy.orm import Session

#: Feature 026: horses pedigree columns loaded for sire/damsire aptitude. Aggregation keys are the
#: NAME columns (sire_name/damsire_name are ~100% populated; the *_id columns are ~0% in the real DB
#: and kept only for the staleness fingerprint / future ID-based migration).
_HORSE_COLUMNS = [
    "horse_id", "sire_name", "dam_name", "damsire_name", "sire_id", "dam_id", "damsire_id",
    # Feature 056: owner/breeder (as-of aggregation keys) + bloodline lines (static categoricals)
    "owner_name", "breeder_name", "sire_line", "damsire_line",
]


@dataclass(frozen=True)
class Frames:
    races: pd.DataFrame
    race_horses: pd.DataFrame
    race_results: pd.DataFrame
    #: Feature 026: per-horse pedigree (optional; default empty keeps existing Frames(...) callers
    #: and make_frames working — pedigree features become all-NaN when absent).
    horses: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=_HORSE_COLUMNS)
    )


def load_frames(
    session: Session,
    end_date: datetime.date | None = None,
    *,
    start_after: datetime.date | None = None,
) -> Frames:
    """Load the feature-source pool. ``start_after`` (Feature 055, default None = unchanged
    behavior) lower-bounds the race window to race_date > start_after — used ONLY to load the
    (end_date, data_through] delta for fingerprint verification, never for feature building
    (features always use the plain end_date-windowed load for static dtype parity)."""
    conn = session.connection()

    races_stmt = (
        select(
            Race.race_id, Race.race_date, Race.venue_code, Race.distance,
            Race.track_type, Race.going, Race.weather, Race.race_class, Race.race_number,
            Race.prize_money,  # Feature 056: pre-published race condition (race_level group)
        )
        .where(Race.race_date >= INGEST_SCOPE_START)
        .order_by(Race.race_date, Race.race_id)
    )
    if end_date is not None:
        races_stmt = races_stmt.where(Race.race_date <= end_date)
    if start_after is not None:
        races_stmt = races_stmt.where(Race.race_date > start_after)
    races = pd.read_sql(races_stmt, conn, parse_dates=["race_date"])

    rh_stmt = (
        select(
            RaceHorse.race_id, RaceHorse.horse_id, RaceHorse.age, RaceHorse.sex,
            RaceHorse.frame, RaceHorse.horse_number, RaceHorse.jockey_id,
            RaceHorse.trainer_id, RaceHorse.weight, RaceHorse.weight_diff,
            RaceHorse.jockey_weight,  # Feature 030: 斤量 (carried weight, pre-race)
            RaceHorse.entry_status,
            RaceHorse.running_style,  # Feature 023: past 脚質 (as-of only, never the target race)
        )
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= INGEST_SCOPE_START)
    )
    rr_stmt = (
        select(
            RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order,
            RaceResult.last_3f, RaceResult.result_status,
            # Feature 023: pace/time result-time data — features aggregate PAST races only (as-of).
            RaceResult.finish_time, RaceResult.finish_time_diff, RaceResult.corner_orders,
            RaceResult.first_3f,  # Feature 056: テン3F — as-of only, same discipline as last_3f
        )
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(Race.race_date >= INGEST_SCOPE_START)
    )
    if end_date is not None:
        rh_stmt = rh_stmt.where(Race.race_date <= end_date)
        rr_stmt = rr_stmt.where(Race.race_date <= end_date)
    if start_after is not None:
        rh_stmt = rh_stmt.where(Race.race_date > start_after)
        rr_stmt = rr_stmt.where(Race.race_date > start_after)

    race_horses = pd.read_sql(rh_stmt, conn)
    race_results = pd.read_sql(rr_stmt, conn)

    # Feature 026: per-horse pedigree (static attribute; no date filter — the as-of leak boundary
    # is enforced by the aggregation over race_results, not by which horse rows are loaded).
    horses_stmt = select(
        Horse.horse_id, Horse.sire_name, Horse.dam_name, Horse.damsire_name,
        Horse.sire_id, Horse.dam_id, Horse.damsire_id,
        # Feature 056: owner/breeder keys + bloodline lines
        Horse.owner_name, Horse.breeder_name, Horse.sire_line, Horse.damsire_line,
    )
    horses = pd.read_sql(horses_stmt, conn)
    return Frames(
        races=races, race_horses=race_horses, race_results=race_results, horses=horses
    )
