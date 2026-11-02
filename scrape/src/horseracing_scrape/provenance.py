"""来歴 2 表の書込み(Feature 176, data-model §2, INV-P1..P5).

``source`` は「その項目群の現在値を採用した経路」。値と同じ savepoint で書く(呼び出し側の責任)。
``observed_at`` は HTTP 取得時刻(呼び出し側が渡す)。既存行は作らない(backfill なし)。
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from horseracing_db.models import RaceFieldProvenance, RaceHorseFieldProvenance
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class HorseProv:
    source: str
    observed_at: datetime.datetime


@dataclass(frozen=True)
class RaceProv:
    source: str
    official_at: datetime.datetime | None
    observed_at: datetime.datetime


@dataclass
class ProvenanceSnapshot:
    """What is currently recorded for one race (read under the race lock)."""

    horse: dict[tuple[str, str], HorseProv] = field(default_factory=dict)  # (horse_id, group)
    race: dict[str, RaceProv] = field(default_factory=dict)  # group

    def horse_source(self, horse_id: str, group: str) -> str | None:
        p = self.horse.get((horse_id, group))
        return None if p is None else p.source

    def horse_observed_at(self, horse_id: str, group: str) -> datetime.datetime | None:
        p = self.horse.get((horse_id, group))
        return None if p is None else p.observed_at

    def race_source(self, group: str) -> str | None:
        p = self.race.get(group)
        return None if p is None else p.source


def read_provenance(session: Session, race_id: str) -> ProvenanceSnapshot:
    snap = ProvenanceSnapshot()
    for row in session.execute(
        select(
            RaceHorseFieldProvenance.horse_id,
            RaceHorseFieldProvenance.field_group,
            RaceHorseFieldProvenance.source,
            RaceHorseFieldProvenance.observed_at,
        ).where(RaceHorseFieldProvenance.race_id == race_id)
    ):
        snap.horse[(row[0], row[1])] = HorseProv(row[2], row[3])
    for row in session.execute(
        select(
            RaceFieldProvenance.field_group,
            RaceFieldProvenance.source,
            RaceFieldProvenance.official_at,
            RaceFieldProvenance.observed_at,
        ).where(RaceFieldProvenance.race_id == race_id)
    ):
        snap.race[row[0]] = RaceProv(row[1], row[2], row[3])
    return snap


def write_horse_provenance(
    session: Session,
    race_id: str,
    horse_id: str,
    group: str,
    *,
    source: str,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
) -> None:
    stmt = insert(RaceHorseFieldProvenance).values(
        race_id=race_id,
        horse_id=horse_id,
        field_group=group,
        source=source,
        observed_at=observed_at,
        ingestion_job_id=job_id,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["race_id", "horse_id", "field_group"],
            set_={
                "source": stmt.excluded.source,
                "observed_at": stmt.excluded.observed_at,
                "ingestion_job_id": stmt.excluded.ingestion_job_id,
            },
        )
    )


def write_horse_provenance_many(
    session: Session,
    race_id: str,
    items: Iterable[tuple[str, str]],
    *,
    source: str,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
) -> None:
    for horse_id, group in items:
        write_horse_provenance(
            session, race_id, horse_id, group, source=source, observed_at=observed_at, job_id=job_id
        )


def write_race_provenance(
    session: Session,
    race_id: str,
    group: str,
    *,
    source: str,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
    official_at: datetime.datetime | None = None,
) -> None:
    stmt = insert(RaceFieldProvenance).values(
        race_id=race_id,
        field_group=group,
        source=source,
        official_at=official_at,
        observed_at=observed_at,
        ingestion_job_id=job_id,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["race_id", "field_group"],
            set_={
                "source": stmt.excluded.source,
                "official_at": stmt.excluded.official_at,
                "observed_at": stmt.excluded.observed_at,
                "ingestion_job_id": stmt.excluded.ingestion_job_id,
            },
        )
    )
