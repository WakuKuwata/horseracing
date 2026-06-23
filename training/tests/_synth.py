"""Insert synthetic multi-year race data for training integration tests."""

from __future__ import annotations

import datetime

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult
from sqlalchemy.orm import Session


def insert_race(
    session: Session,
    *,
    race_id: str,
    race_date: datetime.date,
    horses: list[dict],
) -> None:
    """horses: [{horse_id, horse_number, age, odds, popularity, finish_order,
    entry_status=started, result_status=finished}]."""
    session.add(
        Race(
            race_id=race_id,
            race_number=int(race_id[-2:]),
            race_date=race_date,
            venue_code="05",
            distance=1600,
            track_type="芝",
            going="良",
            weather="晴",
            race_class="未勝利",
        )
    )
    for h in horses:
        hid = h["horse_id"]
        session.merge(Horse(horse_id=hid, horse_name=hid))
        entry_status = h.get("entry_status", EntryStatus.STARTED)
        session.add(
            RaceHorse(
                race_id=race_id,
                horse_id=hid,
                horse_number=h.get("horse_number"),
                age=h.get("age"),
                odds=h.get("odds"),
                popularity=h.get("popularity"),
                entry_status=entry_status,
            )
        )
        if entry_status == EntryStatus.STARTED and h.get("finish_order") is not None:
            session.add(
                RaceResult(
                    race_id=race_id,
                    horse_id=hid,
                    finish_order=h["finish_order"],
                    result_status=h.get("result_status", ResultStatus.FINISHED),
                )
            )
    session.commit()


def seed_learnable(
    session: Session,
    *,
    years: tuple[int, ...] = (2007, 2008, 2009),
    races_per_year: int = 12,
    field_size: int = 8,
) -> None:
    """Seed a dataset with a clean leak-free signal: horse_number 1 always wins.

    horse_number is a POST_FRAME (model-input) feature, so a win-LightGBM can learn it
    and beat the uniform baseline on win LogLoss, while no result-time data is used.
    """
    for year in years:
        for r in range(1, races_per_year + 1):
            race_id = f"{year}0101{r:02d}01"
            horses = []
            for i in range(field_size):
                num = i + 1
                # winner is horse_number 1; the rest fill positions 2..N
                finish = 1 if num == 1 else num
                horses.append(
                    {
                        "horse_id": f"{year}-{r:02d}-H{num}",
                        "horse_number": num,
                        "age": 3 + (i % 3),
                        "odds": 2.0 + 2.0 * i,
                        "popularity": num,
                        "finish_order": finish,
                    }
                )
            insert_race(
                session,
                race_id=race_id,
                race_date=datetime.date(year, 1, 1) + datetime.timedelta(days=r),
                horses=horses,
            )
