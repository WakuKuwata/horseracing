"""Insert synthetic race data into the DB for integration tests."""

from __future__ import annotations

import datetime

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult
from sqlalchemy.orm import Session


def insert_run(
    session: Session,
    *,
    race_id: str,
    race_date: datetime.date,
    horse_id: str,
    finish_order: int | None = None,
    entry_status: str = EntryStatus.STARTED,
    result_status: str | None = ResultStatus.FINISHED,
) -> None:
    session.merge(Race(
        race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
        venue_code="05", distance=1600, track_type="芝", going="良", weather="晴",
        race_class="未勝利",
    ))
    session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
    session.merge(RaceHorse(
        race_id=race_id, horse_id=horse_id, horse_number=1, entry_status=entry_status,
        age=3, sex="牡", frame=1,
    ))
    if entry_status == EntryStatus.STARTED and result_status is not None:
        session.merge(RaceResult(
            race_id=race_id, horse_id=horse_id, finish_order=finish_order,
            result_status=result_status, last_3f=35.0,
        ))
    session.commit()
