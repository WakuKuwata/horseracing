"""Synthetic data for live-serving tests: trainable history + active model + a result-pending race.

`seed_learnable` + `make_active_model` build history and an adopted model (with results).
`seed_pending_race` adds an upcoming race: entries + pre-race odds, NO race_results (= result-pending),
so live-serve can predict/recommend on it. `add_results` flips a race to non-pending (guard tests).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import save_model_version
from horseracing_training.predictor import LightGBMPredictor
from sqlalchemy.orm import Session


def _insert_race(session, *, race_id, race_date, horses, with_results):
    session.add(
        Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
             venue_code="05", distance=1600, track_type="芝", going="良", weather="晴",
             race_class="未勝利")
    )
    for h in horses:
        session.merge(Horse(horse_id=h["horse_id"], horse_name=h["horse_id"]))
        if h.get("jockey_id"):
            session.merge(Jockey(jockey_id=h["jockey_id"], jockey_name=h["jockey_id"]))
        if h.get("trainer_id"):
            session.merge(Trainer(trainer_id=h["trainer_id"], trainer_name=h["trainer_id"]))
    session.flush()
    for h in horses:
        status = h.get("entry_status", EntryStatus.STARTED)
        odds = h.get("odds")
        session.add(RaceHorse(
            race_id=race_id, horse_id=h["horse_id"], horse_number=h.get("horse_number"),
            age=h.get("age"), jockey_id=h.get("jockey_id"), trainer_id=h.get("trainer_id"),
            odds=Decimal(str(odds)) if odds is not None else None,
            popularity=h.get("popularity"), entry_status=status,
        ))
        if with_results and status == EntryStatus.STARTED and h.get("finish_order") is not None:
            session.add(RaceResult(
                race_id=race_id, horse_id=h["horse_id"], finish_order=h["finish_order"],
                result_status=ResultStatus.FINISHED,
            ))
    session.commit()


def seed_learnable(session: Session, *, years=(2007, 2008), races_per_year=10, field_size=8) -> None:
    for year in years:
        for r in range(1, races_per_year + 1):
            race_id = f"{year}0101{r:02d}01"
            horses = [
                {"horse_id": f"{year}-{r:02d}-H{i + 1}", "horse_number": i + 1,
                 "age": 3 + (i % 3), "jockey_id": f"J{i % 4}", "trainer_id": f"T{i % 3}",
                 "odds": 2.0 + 2.0 * i, "popularity": i + 1, "finish_order": 1 if i == 0 else i + 1}
                for i in range(field_size)
            ]
            _insert_race(session, race_id=race_id,
                         race_date=datetime.date(year, 1, 1) + datetime.timedelta(days=r),
                         horses=horses, with_results=True)


def make_active_model(session: Session, artifacts_root, *, model_version="live-test") -> str:
    races = load_eval_races(session)
    result = evaluate(LightGBMPredictor(session, seed=42), races, first_valid_year=2008)
    final = LightGBMPredictor(session, seed=42)
    final.fit([er.context for er in races])
    save_model_version(
        session, model_version=model_version, predictor=final, eval_result=result,
        decision=AdoptionDecision(adopted=True, reasons={}),
        gate=AdoptionGate(ece_threshold=1.0), artifacts_root=str(artifacts_root),
        feature_version="features-004", git_sha=None,
    )
    return model_version


def seed_pending_race(
    session: Session, *, race_id: str, race_date: datetime.date, field_size: int = 8,
    with_odds: bool = True, extra_horse: bool = False,
) -> None:
    """Upcoming race: entries + pre-race odds, NO results (= result-pending). Reuses known horse_ids
    so history features exist; ``extra_horse`` adds a debut (unmapped-like) horse with no history."""
    horses = [
        {"horse_id": f"2008-01-H{i + 1}", "horse_number": i + 1, "age": 4, "jockey_id": f"J{i % 4}",
         "trainer_id": f"T{i % 3}", "odds": (2.0 + 2.0 * i) if with_odds else None,
         "popularity": i + 1}
        for i in range(field_size)
    ]
    if extra_horse:  # debut horse with no prior history (Unknown features), still in the field
        horses.append({"horse_id": f"DEBUT-{race_id}", "horse_number": field_size + 1, "age": 3,
                       "jockey_id": "J0", "trainer_id": "T0",
                       "odds": 50.0 if with_odds else None, "popularity": field_size + 1})
    _insert_race(session, race_id=race_id, race_date=race_date, horses=horses, with_results=False)


def add_results(session: Session, *, race_id: str) -> None:
    """Flip a race to non-pending by inserting FINISHED results for its started horses."""
    rhs = session.query(RaceHorse).filter(RaceHorse.race_id == race_id).all()
    for i, rh in enumerate(rhs, start=1):
        if rh.entry_status == EntryStatus.STARTED:
            session.add(RaceResult(race_id=race_id, horse_id=rh.horse_id, finish_order=i,
                                   result_status=ResultStatus.FINISHED))
    session.commit()
