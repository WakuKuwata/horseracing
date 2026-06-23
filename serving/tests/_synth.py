"""Synthetic data + an adopted (active) model with artifacts, for serving tests."""

from __future__ import annotations

import datetime

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate
from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import save_model_version
from horseracing_training.predictor import LightGBMPredictor
from sqlalchemy.orm import Session


def insert_race(session: Session, *, race_id: str, race_date: datetime.date, horses: list[dict]) -> None:
    session.add(
        Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
             venue_code="05", distance=1600, track_type="芝", going="良", weather="晴",
             race_class="未勝利")
    )
    # parents first (FK targets), flush, then children — avoids FK-order autoflush violations
    for h in horses:
        session.merge(Horse(horse_id=h["horse_id"], horse_name=h["horse_id"]))
        if h.get("jockey_id"):
            session.merge(Jockey(jockey_id=h["jockey_id"], jockey_name=h["jockey_id"]))
        if h.get("trainer_id"):
            session.merge(Trainer(trainer_id=h["trainer_id"], trainer_name=h["trainer_id"]))
    session.flush()
    for h in horses:
        entry_status = h.get("entry_status", EntryStatus.STARTED)
        session.add(
            RaceHorse(
                race_id=race_id, horse_id=h["horse_id"], horse_number=h.get("horse_number"),
                age=h.get("age"), jockey_id=h.get("jockey_id"), trainer_id=h.get("trainer_id"),
                odds=h.get("odds"), popularity=h.get("popularity"), entry_status=entry_status,
            )
        )
        if entry_status == EntryStatus.STARTED and h.get("finish_order") is not None:
            session.add(
                RaceResult(
                    race_id=race_id, horse_id=h["horse_id"], finish_order=h["finish_order"],
                    result_status=h.get("result_status", ResultStatus.FINISHED),
                )
            )
    session.commit()


def seed_learnable(session: Session, *, years=(2007, 2008), races_per_year=10, field_size=8) -> None:
    """horse_number 1 always wins (a leak-free, model-input signal)."""
    for year in years:
        for r in range(1, races_per_year + 1):
            race_id = f"{year}0101{r:02d}01"
            horses = [
                {
                    "horse_id": f"{year}-{r:02d}-H{i + 1}", "horse_number": i + 1,
                    "age": 3 + (i % 3), "jockey_id": f"J{i % 4}", "trainer_id": f"T{i % 3}",
                    "odds": 2.0 + 2.0 * i, "popularity": i + 1,
                    "finish_order": 1 if i == 0 else i + 1,
                }
                for i in range(field_size)
            ]
            insert_race(session, race_id=race_id,
                        race_date=datetime.date(year, 1, 1) + datetime.timedelta(days=r),
                        horses=horses)


def make_active_model(session: Session, artifacts_root, *, model_version="serv-test",
                      target_encode=()) -> str:
    """Train a win model on the seeded data and save it as an ACTIVE model_version + artifacts."""
    races = load_eval_races(session)
    evald = LightGBMPredictor(session, seed=42, target_encode_cols=tuple(target_encode))
    result = evaluate(evald, races, first_valid_year=2008)
    final = LightGBMPredictor(session, seed=42, target_encode_cols=tuple(target_encode))
    final.fit([er.context for er in races])
    save_model_version(
        session, model_version=model_version, predictor=final, eval_result=result,
        decision=AdoptionDecision(adopted=True, reasons={}),
        gate=AdoptionGate(ece_threshold=1.0), artifacts_root=str(artifacts_root),
        feature_version="features-004", git_sha=None,
    )
    return model_version
