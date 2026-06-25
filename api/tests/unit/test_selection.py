"""T009 (014): deterministic prediction_run selection + canonical population (SC-002)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import AdoptionStatus, EntryStatus
from horseracing_db.models import PredictionRun

from horseracing_api.selection import canonical_win_probs, select_prediction_run
from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"


def test_active_model_run_preferred(session):
    seed_model(session, model_version="m-active", adoption=AdoptionStatus.ACTIVE)
    seed_model(session, model_version="m-cand", adoption=AdoptionStatus.CANDIDATE)
    # candidate run first (older), then active run (newer) — active must win regardless
    seed_race(session, race_id=_RACE, model_version="m-cand",
              horses={1: {"win": 0.5, "odds": 2.0}, 2: {"win": 0.5, "odds": 3.0}})
    active_run = seed_race(session, race_id=_RACE, model_version="m-active",
                           horses={1: {"win": 0.6, "odds": 2.0}, 2: {"win": 0.4, "odds": 3.0}})
    chosen = select_prediction_run(session, _RACE)
    assert chosen is not None and chosen.prediction_run_id == active_run
    assert chosen.model_version == "m-active"


def test_no_run_returns_none(session):
    assert select_prediction_run(session, "200806010199") is None


def test_canonical_excludes_scratched_and_nonpositive(session):
    seed_model(session)
    run = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.5, "odds": 2.0},
        2: {"win": 0.3, "odds": 3.0, "status": EntryStatus.CANCELLED},  # scratched -> excluded
        3: {"win": 0.0, "odds": 5.0},                                    # zero prob -> excluded
    })
    cw = canonical_win_probs(session, run_id=run, race_id=_RACE)
    assert set(cw) == {1}  # only the started, positive-prob horse


def test_tiebreak_by_computed_at_then_run_id(session):
    # two runs, same active model, same race -> latest computed_at wins (deterministic)
    seed_model(session)
    r1 = seed_race(session, race_id=_RACE, horses={1: {"win": 0.5, "odds": 2.0},
                                                   2: {"win": 0.5, "odds": 3.0}})
    r2 = seed_race(session, race_id=_RACE, horses={1: {"win": 0.6, "odds": 2.0},
                                                   2: {"win": 0.4, "odds": 3.0}})
    # force r2 to be strictly newer
    run2 = session.get(PredictionRun, r2)
    run2.computed_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)
    session.commit()
    chosen = select_prediction_run(session, _RACE)
    assert chosen.prediction_run_id == r2 and r1 != r2
