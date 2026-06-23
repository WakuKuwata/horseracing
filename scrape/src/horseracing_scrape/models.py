"""Parsed (network-free) dataclasses for netkeiba pages."""

from __future__ import annotations

import datetime
from dataclasses import dataclass


class ParseError(ValueError):
    """Raised when a required element is missing (fail-close — never invent data)."""


@dataclass(frozen=True)
class ScrapedRaceKey:
    year: int
    track_code: str
    kai: int
    nichime: int
    race_no: int


@dataclass(frozen=True)
class ScrapedRace:
    key: ScrapedRaceKey
    race_date: datetime.date | None
    distance: int | None
    track_type: str | None
    going: str | None
    weather: str | None
    race_class: str | None


@dataclass(frozen=True)
class ScrapedEntryHorse:
    netkeiba_horse_id: str
    horse_name: str | None
    frame: int | None
    horse_number: int | None
    netkeiba_jockey_id: str | None
    jockey_name: str | None
    netkeiba_trainer_id: str | None
    trainer_name: str | None
    weight: int | None
    sex: str | None
    age: int | None
    entry_status: str


@dataclass(frozen=True)
class ScrapedEntry:
    race: ScrapedRace
    horses: tuple[ScrapedEntryHorse, ...]


@dataclass(frozen=True)
class ScrapedOddsRow:
    netkeiba_horse_id: str
    odds: float | None
    popularity: int | None


@dataclass(frozen=True)
class ScrapedOdds:
    key: ScrapedRaceKey
    rows: tuple[ScrapedOddsRow, ...]


@dataclass(frozen=True)
class ScrapedResultRow:
    netkeiba_horse_id: str
    finish_order: int | None
    result_status: str
    finish_time: str | None


@dataclass(frozen=True)
class ScrapedResult:
    key: ScrapedRaceKey
    rows: tuple[ScrapedResultRow, ...]
