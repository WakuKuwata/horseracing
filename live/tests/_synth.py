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
from horseracing_features.registry import FEATURE_VERSION
from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION, gate_config_hash
from horseracing_eval.hashing import stable_hash
from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import save_model_version
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.promotion_evidence import stamp_report
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


#: The pre-registered gate-config these fixtures declare. Its numbers are not what any test
#: asserts on; it exists so the stamped report satisfies the REAL confirmatory contract
#: (``eval.decision.assert_confirmatory``): the current contract version, a declared
#: retraining SD, and an eval window that matches the frozen config.
_GATE_WINDOW = {"from": "2008-01-01", "to": "2008-12-31"}
_GATE_CFG = {
    "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
    "seed_noise": {"sd_fold": 0.001816},
    "eval_window": dict(_GATE_WINDOW),
}


class _Arm:
    """One side of a paired evaluation, in the shape ``stamp_report`` consumes."""

    def __init__(self, predictor, label: str):
        self._pred = predictor
        self.recipe_meta = {"arm": label, "seed": predictor.seed}
        self.recipe_hash = stable_hash(self.recipe_meta)


def adopt_verdict(candidate, reference) -> dict:
    """A CONFIRMED paired-eval report that can justify an ACTIVE promotion.

    ``save_model_version`` will not mark a model ACTIVE without one: the legacy 4-metric gate
    alone cannot justify a promotion. Tests that need a genuinely active model therefore have to
    supply this, and supplying it exercises the real contract rather than an escape hatch.

    CONTRACT AS OF 2026-09 (commit ``bdc789e``). An ``ADOPT`` verdict is no longer sufficient on
    its own. ``adoption.evaluate_promotion`` now also requires, and this report therefore carries:

    * ``can_adopt is True`` and ``eligible_for_verdict is True`` — strict booleans, not truthy
      values, and no longer waived when ``artifact_kind`` is absent. An exploratory report can no
      longer be reused as promotion evidence by omitting the field.
    * a ``promotion_evidence`` block (written by ``stamp_report``, never by hand) binding the
      report to the frozen gate-config, the scored window, and BOTH arms' recipe identities.
    * a ``fitted_contract`` per arm that matches the contract recorded when that model was
      REGISTERED — which is why ``make_active_model`` registers a reference model first and
      passes it as ``reference_model_version``.

    The contract version is READ FROM eval, never spelled out here. Hard-coding it is precisely
    how the promotion path broke once already — training pinned "v3" by equality while eval moved
    to "v4", and every valid verdict was silently downgraded to a candidate.
    """
    return stamp_report(
        {
            "decision": "ADOPT",
            "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
            "decision_reason": {"subgroup_assurance": "full"},
        },
        _Arm(candidate, "candidate"),
        _Arm(reference, "active"),
        cfg=_GATE_CFG, confirmed=True, expected_hash=gate_config_hash(_GATE_CFG),
        window=_GATE_WINDOW, feature_version=FEATURE_VERSION,
    )


def make_active_model(session: Session, artifacts_root, *, model_version="live-test") -> str:
    """Train a win model on the seeded data and register it as the ACTIVE model_version.

    Two rows are registered, because since 2026-09 a promotion must be BOUND to a registered
    comparison model: ``<model_version>-ref`` is saved first as a CANDIDATE and named as the
    ``reference_model_version``, so the verdict's "active" arm has a registered fitted
    contract to match. The reference reuses the same fit — these tests need an ACTIVE model,
    not a meaningful accuracy comparison — so no extra training cost is incurred.
    """
    races = load_eval_races(session)
    result = evaluate(LightGBMPredictor(session, seed=42), races, first_valid_year=2008)
    final = LightGBMPredictor(session, seed=42)
    final.fit([er.context for er in races])
    reference_version = f"{model_version}-ref"
    save_model_version(
        session, model_version=reference_version, predictor=final, eval_result=result,
        decision=AdoptionDecision(adopted=True, reasons={}),
        gate=AdoptionGate(ece_threshold=1.0), artifacts_root=str(artifacts_root),
        feature_version=FEATURE_VERSION, git_sha=None, register_as_candidate=True,
    )
    save_model_version(
        session, model_version=model_version, predictor=final, eval_result=result,
        decision=AdoptionDecision(adopted=True, reasons={}),
        verdict=adopt_verdict(final, final),
        gate=AdoptionGate(ece_threshold=1.0), artifacts_root=str(artifacts_root),
        feature_version=FEATURE_VERSION, git_sha=None,
        reference_model_version=reference_version,
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
