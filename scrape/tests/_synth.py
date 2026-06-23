"""Helpers for scrape integration tests."""

from __future__ import annotations

import datetime

from horseracing_db.enums import EntityType, EntryStatus, MappingStatus, ResultStatus, Source
from horseracing_db.models import Horse, IdMapping, Race, RaceHorse, RaceResult
from sqlalchemy.orm import Session

from horseracing_scrape.fetch import FixtureFetcher
from tests.conftest import fixture_html

#: race_id constructed from the fixtures' race key (year 2025, Tokyo 05, kai 2, day 3, race 11)
RACE_ID = "202505020311"


def fixture_fetcher(name: str, url: str = "u") -> tuple[FixtureFetcher, list[str]]:
    return FixtureFetcher({url: fixture_html(name)}), [url]


def seed_finished_race(
    session: Session, *, race_id: str, horse_id: str, race_date: datetime.date
) -> None:
    """A prior finished race so feature history is non-empty (realistic: DB always has JRA-VAN
    history). Lets the unmapped-horse debut check run without the empty-history edge case."""
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
                       venue_code=race_id[4:6]))
    session.merge(Horse(horse_id=horse_id, horse_name=horse_id, data_source="jra_van"))
    session.add(RaceHorse(race_id=race_id, horse_id=horse_id, horse_number=1,
                          entry_status=EntryStatus.STARTED))
    session.add(RaceResult(race_id=race_id, horse_id=horse_id, finish_order=1,
                           result_status=ResultStatus.FINISHED))
    session.commit()


def map_horse(session: Session, *, netkeiba_id: str, canonical_id: str) -> None:
    """Mark a netkeiba horse as MAPPED to a JRA-VAN canonical_id (and create that horse)."""
    session.merge(Horse(horse_id=canonical_id, horse_name=canonical_id, data_source="jra_van"))
    session.add(
        IdMapping(
            entity_type=EntityType.HORSE, source=Source.NETKEIBA, source_id=netkeiba_id,
            canonical_id=canonical_id, mapping_status=MappingStatus.MAPPED,
        )
    )
    session.commit()
