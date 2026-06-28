"""Seed helpers for ops tests."""

from __future__ import annotations

import datetime

from horseracing_db.enums import ResultStatus
from horseracing_db.models import Horse, Race, RaceResult
from sqlalchemy.orm import Session


def seed_race(session: Session, *, race_id: str, race_date=datetime.date(2024, 12, 28)) -> None:
    """A result-pending race (no race_results) so the refresh fetches entries+odds."""
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
                       venue_code=race_id[4:6]))
    session.commit()


def mark_finished(session: Session, *, race_id: str) -> None:
    """Add a race_results row so the race is no longer result-pending (refresh fetches results)."""
    session.merge(Horse(horse_id="seedH", horse_name="seedH", data_source="jra_van"))
    session.merge(RaceResult(race_id=race_id, horse_id="seedH", finish_order=1,
                             result_status=ResultStatus.FINISHED))
    session.commit()
