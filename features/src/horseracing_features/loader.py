"""Load races / race_horses / race_results into pandas (2007+ pool).

Loads the full pool from the 2007 ingest boundary up to ``end_date`` so that
past-performance history is complete even for target races early in a window.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import pandas as pd
from horseracing_db.models import Race, RaceHorse, RaceResult
from horseracing_db.validation import INGEST_SCOPE_START
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Frames:
    races: pd.DataFrame
    race_horses: pd.DataFrame
    race_results: pd.DataFrame


def load_frames(session: Session, end_date: datetime.date | None = None) -> Frames:
    conn = session.connection()

    races_stmt = (
        select(
            Race.race_id, Race.race_date, Race.venue_code, Race.distance,
            Race.track_type, Race.going, Race.weather, Race.race_class, Race.race_number,
        )
        .where(Race.race_date >= INGEST_SCOPE_START)
        .order_by(Race.race_date, Race.race_id)
    )
    if end_date is not None:
        races_stmt = races_stmt.where(Race.race_date <= end_date)
    races = pd.read_sql(races_stmt, conn, parse_dates=["race_date"])

    rh_stmt = (
        select(
            RaceHorse.race_id, RaceHorse.horse_id, RaceHorse.age, RaceHorse.sex,
            RaceHorse.frame, RaceHorse.horse_number, RaceHorse.jockey_id,
            RaceHorse.trainer_id, RaceHorse.weight, RaceHorse.weight_diff,
            RaceHorse.entry_status,
        )
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= INGEST_SCOPE_START)
    )
    rr_stmt = (
        select(
            RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order,
            RaceResult.last_3f, RaceResult.result_status,
        )
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(Race.race_date >= INGEST_SCOPE_START)
    )
    if end_date is not None:
        rh_stmt = rh_stmt.where(Race.race_date <= end_date)
        rr_stmt = rr_stmt.where(Race.race_date <= end_date)

    race_horses = pd.read_sql(rh_stmt, conn)
    race_results = pd.read_sql(rr_stmt, conn)
    return Frames(races=races, race_horses=race_horses, race_results=race_results)
