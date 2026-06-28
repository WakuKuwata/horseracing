"""ORM read queries (Feature 014) — SELECT only, never commit. The single place that touches the DB.

Race listing uses a total stable order (race_date DESC NULLS LAST, venue_code NULLS LAST,
race_number NULLS LAST, race_id) and computes total/has_next on the FILTERED query. Recommendations
are restricted to exotic bet types (win recommendations store a dict selection — out of this
endpoint's list[int] contract).
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import BetType, EntryStatus
from horseracing_db.models import (
    ExoticOdds,
    Horse,
    Jockey,
    ModelVersion,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
    Recommendation,
    Trainer,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _filtered_races(date: datetime.date | None, venue: str | None):
    stmt = select(Race)
    if date is not None:
        stmt = stmt.where(Race.race_date == date)
    if venue is not None:
        stmt = stmt.where(Race.venue_code == venue)
    return stmt


def list_races(
    session: Session, *, date: datetime.date | None, venue: str | None, page: int, page_size: int
) -> tuple[list[Race], int]:
    base = _filtered_races(date, venue)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(
            Race.race_date.desc().nulls_last(),
            Race.venue_code.asc().nulls_last(),
            Race.race_number.asc().nulls_last(),
            Race.race_id.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), int(total)


def get_race(session: Session, race_id: str) -> Race | None:
    return session.get(Race, race_id)


def race_horses(session: Session, race_id: str):
    """Entry rows joined with horse/jockey/trainer NAMES (so the UI shows names, not ids).

    LEFT join jockey/trainer (a row may lack them). Returns SQLAlchemy rows exposing both the
    RaceHorse columns and horse_name/jockey_name/trainer_name.
    """
    return session.execute(
        select(
            RaceHorse.horse_number, RaceHorse.horse_id, RaceHorse.frame,
            RaceHorse.entry_status, RaceHorse.age, RaceHorse.sex,
            RaceHorse.weight, RaceHorse.weight_diff, RaceHorse.jockey_weight,
            RaceHorse.odds, RaceHorse.popularity,
            Horse.horse_name,
            Jockey.jockey_name,
            Trainer.trainer_name,
        )
        .select_from(RaceHorse)
        .outerjoin(Horse, Horse.horse_id == RaceHorse.horse_id)
        .outerjoin(Jockey, Jockey.jockey_id == RaceHorse.jockey_id)
        .outerjoin(Trainer, Trainer.trainer_id == RaceHorse.trainer_id)
        .where(RaceHorse.race_id == race_id)
        .order_by(RaceHorse.horse_number.asc().nulls_last(), RaceHorse.horse_id.asc())
    ).all()


def run_predictions(session: Session, *, run_id, race_id: str):
    """(horse_number, horse_id, entry_status, win, top2, top3) for the run's started horses."""
    return session.execute(
        select(
            RaceHorse.horse_number, RaceHorse.horse_id, RaceHorse.entry_status,
            RacePrediction.win_prob, RacePrediction.top2_prob, RacePrediction.top3_prob,
        )
        .join(RacePrediction, RacePrediction.horse_id == RaceHorse.horse_id)
        .where(RaceHorse.race_id == race_id)
        .where(RacePrediction.prediction_run_id == run_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .order_by(RaceHorse.horse_number.asc().nulls_last(), RaceHorse.horse_id.asc())
    ).all()


def win_odds(session: Session, race_id: str):
    """(horse_number, horse_id, odds, updated_at) for started horses with odds."""
    return session.execute(
        select(RaceHorse.horse_number, RaceHorse.horse_id, RaceHorse.odds, RaceHorse.updated_at)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .order_by(RaceHorse.horse_number.asc().nulls_last(), RaceHorse.horse_id.asc())
    ).all()


def canonical_win_odds(session: Session, race_id: str) -> dict[int, float]:
    """{horse_number -> win odds} for started horses with valid (>0) odds (010 input population)."""
    out: dict[int, float] = {}
    for horse_number, _hid, odds, _u in win_odds(session, race_id):
        if horse_number is None or odds is None or float(odds) <= 0.0:
            continue
        out[int(horse_number)] = float(odds)
    return out


def prior_start_counts(session: Session, race_id: str) -> dict[str, int]:
    """Feature 021 US3: {horse_id -> number of STARTED races strictly BEFORE this race's date}.

    Leak-safe data-backing signal: uses entries (not results), date strictly before the target race.
    Horses with no prior starts are absent (caller treats absent as 0 = weak backing).
    """
    target = session.get(Race, race_id)
    if target is None or target.race_date is None:
        return {}
    horse_ids = list(
        session.scalars(
            select(RaceHorse.horse_id)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        )
    )
    if not horse_ids:
        return {}
    rows = session.execute(
        select(RaceHorse.horse_id, func.count())
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(RaceHorse.horse_id.in_(horse_ids))
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .where(Race.race_date < target.race_date)
        .group_by(RaceHorse.horse_id)
    ).all()
    return {hid: int(c) for hid, c in rows}


def race_has_results(session: Session, race_id: str) -> bool:
    """True if any race_results exist (Feature 021: odds_source final vs prerace)."""
    return session.scalar(
        select(func.count()).select_from(RaceResult).where(RaceResult.race_id == race_id)
    ) not in (None, 0)


def race_ids_with_results(session: Session, race_ids: list[str]) -> set[str]:
    """The subset of race_ids that have any race_results (bulk, one query for a listing page)."""
    if not race_ids:
        return set()
    rows = session.scalars(
        select(RaceResult.race_id).where(RaceResult.race_id.in_(race_ids)).distinct()
    ).all()
    return set(rows)


def win_odds_as_of(session: Session, race_id: str):
    """Max updated_at across started-horse win odds (Feature 021 odds audit), or None."""
    return session.scalar(
        select(func.max(RaceHorse.updated_at))
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    )


def model_metrics_summary(session: Session, model_version: str) -> tuple[bool, dict | None]:
    """(exists, metrics_summary) for a model_version (Feature 021 US2 calibration read-only)."""
    row = session.get(ModelVersion, model_version)
    if row is None:
        return False, None
    return True, (row.metrics_summary or None)


def real_exotic_odds(session: Session, race_id: str) -> list[ExoticOdds]:
    return list(
        session.scalars(
            select(ExoticOdds).where(ExoticOdds.race_id == race_id).order_by(ExoticOdds.bet_type)
        )
    )


def exotic_recommendations(session: Session, race_id: str) -> list[Recommendation]:
    """Persisted exotic recommendations only (win recs have a dict selection — excluded)."""
    return list(
        session.scalars(
            select(Recommendation)
            .where(Recommendation.race_id == race_id)
            .where(Recommendation.bet_type.in_(BetType.EXOTIC))
            .order_by(Recommendation.bet_type, Recommendation.computed_at.desc())
        )
    )
