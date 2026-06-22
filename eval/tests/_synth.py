"""Helpers to insert synthetic race data for integration tests."""

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
    """horses: [{horse_id, horse_number, odds, finish_order,
    entry_status=started, result_status=finished}]."""
    session.add(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date))
    for h in horses:
        hid = h["horse_id"]
        session.merge(Horse(horse_id=hid, horse_name=hid))
        entry_status = h.get("entry_status", EntryStatus.STARTED)
        session.add(
            RaceHorse(
                race_id=race_id, horse_id=hid, horse_number=h.get("horse_number"),
                odds=h.get("odds"), popularity=h.get("popularity"), entry_status=entry_status,
            )
        )
        if entry_status == EntryStatus.STARTED and h.get("finish_order") is not None:
            session.add(
                RaceResult(
                    race_id=race_id, horse_id=hid, finish_order=h["finish_order"],
                    result_status=h.get("result_status", ResultStatus.FINISHED),
                )
            )
    session.commit()


def make_informative_field(n: int, winner: int) -> list[dict]:
    """A field where lower odds correlate with finishing position (favorite=winner)."""
    horses = []
    for i in range(n):
        # odds increase with horse index; winner gets the lowest odds.
        order = (i - winner) % n  # 0 for winner
        horses.append(
            {
                "horse_id": f"H{i}",
                "horse_number": i + 1,
                "odds": 2.0 + 2.0 * order,
                "finish_order": order + 1,
            }
        )
    return horses
