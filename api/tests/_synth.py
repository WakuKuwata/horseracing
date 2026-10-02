"""Synthetic seeding for api integration tests (races / runs / predictions / odds / recommendations)."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from horseracing_db.enums import AdoptionStatus, BetType, EntryStatus, ResultStatus
from horseracing_db.models import (
    ExoticOdds,
    Horse,
    MarketEvPrediction,
    ModelVersion,
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
    Recommendation,
)
from horseracing_eval.attention_rules import DISPLAYED_MARKET_EV_MODEL_VERSION
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
                explanation=h.get("explanation"),  # Feature 040: JSONB or None
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
                       is_estimated=True, stake_fraction=None):
    session.add(Recommendation(
        prediction_run_id=run_id, race_id=race_id, bet_type=bet_type, selection=list(selection),
        market_odds_used=(None if is_estimated else Decimal("12.0")),
        estimated_market_odds_used=(Decimal("9.0") if is_estimated else None),
        is_estimated_odds=is_estimated, pseudo_odds=Decimal("5.0"), pseudo_roi=Decimal("0.5"),
        stake_fraction=(Decimal(str(stake_fraction)) if stake_fraction is not None else None),
        logic_version="rec-lv",
    ))
    session.commit()


def seed_market_ev(
    session: Session,
    *,
    race_id: str,
    horses: dict[int, dict],  # horse_number -> {win_prob, odds_used, expected_return?, horse_id?, ...}
    model_version=DISPLAYED_MARKET_EV_MODEL_VERSION,
    logic_version="mev-v1;test",
    computed_at=datetime.datetime(2026, 9, 27, 3, 0, tzinfo=datetime.UTC),
    odds_observed_at=datetime.datetime(2026, 9, 27, 2, 50, tzinfo=datetime.UTC),
    result_pending=True,
    booster="model_2026.txt",
):
    """Feature 137: persist market-aware expected-return rows (one compute run) for a race.

    expected_return defaults to win_prob × odds_used (as the training job stores it). Per-horse
    overrides: horse_id, expected_return, odds_observed_at, result_pending.
    """
    run_id = uuid.uuid4()
    for n, h in horses.items():
        win_prob = Decimal(str(h["win_prob"]))
        odds_used = Decimal(str(h["odds_used"]))
        expected = (Decimal(str(h["expected_return"])) if "expected_return" in h
                    else win_prob * odds_used)
        session.add(MarketEvPrediction(
            race_id=race_id, model_version=model_version, horse_id=h.get("horse_id", f"H{n}"),
            horse_number=n, win_prob=win_prob, odds_used=odds_used, expected_return=expected,
            odds_observed_at=h.get("odds_observed_at", odds_observed_at),
            result_pending_at_compute=h.get("result_pending", result_pending),
            booster=booster, booster_sha256="0" * 64, logic_version=logic_version,
            run_id=run_id, computed_at=computed_at,
        ))
    session.commit()
    return run_id
