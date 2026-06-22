"""Build evaluation data from the DB (data-model.md).

Population = started horses (entry_status excludes cancelled/excluded). Scoring
labels come from labels.derive_labels (finished only, dead-heat aware). Races with
no finished horses are dropped.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from horseracing_db.enums import EntryStatus
from horseracing_db.labels import derive_labels
from horseracing_db.models import Race, RaceHorse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .predictor import HorseEntry, RaceContext, ResultMarket


@dataclass(frozen=True)
class ScoringLabel:
    horse_id: str
    win: int
    top2: int
    top3: int


@dataclass(frozen=True)
class EvalRace:
    context: RaceContext
    labels: tuple[ScoringLabel, ...]  # finished horses only


def load_eval_races(
    session: Session,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> list[EvalRace]:
    """Load races (sorted by race_date, race_id) with started population + finished labels."""
    stmt = select(Race).order_by(Race.race_date, Race.race_id)
    if start_date is not None:
        stmt = stmt.where(Race.race_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Race.race_date <= end_date)

    eval_races: list[EvalRace] = []
    for race in session.scalars(stmt):
        started = session.scalars(
            select(RaceHorse)
            .where(RaceHorse.race_id == race.race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
            .order_by(RaceHorse.horse_number, RaceHorse.horse_id)
        ).all()
        if not started:
            continue

        horses = tuple(
            HorseEntry(
                horse_id=rh.horse_id,
                frame=rh.frame,
                horse_number=rh.horse_number,
                result_market=ResultMarket(
                    odds=float(rh.odds) if rh.odds is not None else None,
                    popularity=rh.popularity,
                ),
            )
            for rh in started
        )

        labels = tuple(
            ScoringLabel(
                horse_id=row["horse_id"], win=row["win"], top2=row["top2"], top3=row["top3"]
            )
            for row in derive_labels(session, race.race_id)
        )
        if not labels:  # all non-finishers -> excluded from evaluation
            continue

        eval_races.append(
            EvalRace(
                context=RaceContext(
                    race_id=race.race_id, race_date=race.race_date, started_horses=horses
                ),
                labels=labels,
            )
        )
    return eval_races
