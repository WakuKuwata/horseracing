"""Synthetic seeding for api integration tests (races / runs / predictions / odds / recommendations)."""

from __future__ import annotations

import datetime
from decimal import Decimal

from horseracing_db.enums import AdoptionStatus, BetType, EntryStatus, ResultStatus
from horseracing_db.models import (
    ExoticOdds,
    Horse,
    ModelVersion,
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
    Recommendation,
)
from sqlalchemy.orm import Session


def seed_model(session: Session, *, model_version="m-active", adoption=AdoptionStatus.ACTIVE) -> str:
    session.merge(ModelVersion(model_version=model_version, model_family="test",
                               adoption_status=adoption))
    session.commit()
    return model_version


def seed_race(
    session: Session,
    *,
    race_id: str,
    race_date=datetime.date(2008, 6, 1),
    venue_code="05",
    race_number=1,
    horses: dict[int, dict],   # horse_number -> {win, top2, top3, odds, status, finish}
    model_version="m-active",
):
    """Seed a race + started horses + a prediction_run with predictions + win odds (+ results)."""
    session.merge(Race(race_id=race_id, race_number=race_number, race_date=race_date,
                       venue_code=venue_code))
    for n in horses:
        session.merge(Horse(horse_id=f"H{n}", horse_name=f"H{n}"))
    session.flush()
    run = PredictionRun(race_id=race_id, model_version=model_version, logic_version="lv-test")
    session.add(run)
    session.flush()
    for n, h in horses.items():
        status = h.get("status", EntryStatus.STARTED)
        # merge so seeding the same race twice (multiple runs) doesn't violate the race_horses PK
        session.merge(RaceHorse(race_id=race_id, horse_id=f"H{n}", horse_number=n,
                                odds=(Decimal(str(h["odds"])) if h.get("odds") is not None else None),
                                entry_status=status))
        if "win" in h:
            session.add(RacePrediction(
                prediction_run_id=run.prediction_run_id, horse_id=f"H{n}",
                win_prob=Decimal(str(h["win"])), top2_prob=Decimal(str(h.get("top2", h["win"]))),
                top3_prob=Decimal(str(h.get("top3", h["win"]))),
            ))
        if h.get("finish") is not None and status == EntryStatus.STARTED:
            session.merge(RaceResult(race_id=race_id, horse_id=f"H{n}", finish_order=h["finish"],
                                     result_status=ResultStatus.FINISHED))
    session.commit()
    return run.prediction_run_id


def add_exotic_odds(session, *, race_id, bet_type, selection, odds, coverage="partial"):
    session.add(ExoticOdds(race_id=race_id, bet_type=bet_type, selection=selection,
                           odds=Decimal(str(odds)), coverage_scope=coverage, source="netkeiba"))
    session.commit()


def add_recommendation(session, *, race_id, run_id, bet_type=BetType.EXACTA, selection=(1, 2),
                       is_estimated=True):
    session.add(Recommendation(
        prediction_run_id=run_id, race_id=race_id, bet_type=bet_type, selection=list(selection),
        market_odds_used=(None if is_estimated else Decimal("12.0")),
        estimated_market_odds_used=(Decimal("9.0") if is_estimated else None),
        is_estimated_odds=is_estimated, pseudo_odds=Decimal("5.0"), pseudo_roi=Decimal("0.5"),
        logic_version="rec-lv",
    ))
    session.commit()
