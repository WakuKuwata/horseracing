"""A field change invalidates all heads until the selected model predicts the new field."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import AdoptionStatus, EntryStatus
from horseracing_db.models import Horse, PredictionRun, RaceHorse
from horseracing_eval.baselines import harville_topk

from horseracing_api.selection import (
    canonical_win_probs,
    prediction_population_matches,
    select_prediction_run,
)
from tests._synth import add_recommendation, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"


def _horses(probabilities):
    top2, top3 = harville_topk(list(probabilities))
    return {
        i: {"win": p, "top2": top2[i - 1], "top3": top3[i - 1], "odds": 1 / p}
        for i, p in enumerate(probabilities, 1)
    }


@pytest.mark.parametrize("status", [EntryStatus.CANCELLED, EntryStatus.EXCLUDED])
def test_field_change_is_unavailable_until_fresh_prediction(client, session, status):
    seed_model(session)
    old_run = seed_race(session, race_id=_RACE, horses=_horses([0.4, 0.3, 0.2, 0.1]))
    add_recommendation(session, race_id=_RACE, run_id=old_run, selection=(1, 2))
    url = f"/api/v1/races/{_RACE}/predictions"
    before = client.get(url).json()
    assert before["run"]["prediction_run_id"] == str(old_run)
    assert [h["win"] for h in before["horses"]] == [0.4, 0.3, 0.2, 0.1]

    session.get(RaceHorse, (_RACE, "H1")).entry_status = status
    session.commit()
    assert select_prediction_run(session, _RACE) is None
    assert canonical_win_probs(session, run_id=old_run, race_id=_RACE) == {}
    for params in ({}, {"bet_type": "exacta"}):
        response = client.get(url, params=params)
        assert response.status_code == 200
        body = response.json()
        assert body["run"] is None and body["horses"] == [] and body["joint"] is None
        assert body["canonical_consistent"] is None
    explicit = client.get(url, params={"model_version": "m-active"})
    assert explicit.status_code == 404
    assert explicit.json()["code"] == "prediction_unavailable"
    recs = client.get(f"/api/v1/races/{_RACE}/recommendations").json()
    assert recs["items"] == [] and recs["win_policy_status"] == "no_run"

    # The prediction producer recomputes all three heads on the remaining field.
    remaining = _horses([0.5, 1 / 3, 1 / 6])
    fresh_horses = {n + 1: values for n, values in remaining.items()}
    fresh_horses[1] = {"status": status, "odds": 2.5}  # no prediction for a nonstarter
    fresh_run = seed_race(session, race_id=_RACE, horses=fresh_horses)
    for params in ({}, {"model_version": "m-active"}):
        response = client.get(url, params=params)
        assert response.status_code == 200
        body = response.json()
        assert body["run"]["prediction_run_id"] == str(fresh_run)
        assert {h["horse_number"] for h in body["horses"]} == {2, 3, 4}
        for head, total in (("win", 1), ("top2", 2), ("top3", 3)):
            assert sum(h[head] for h in body["horses"]) == pytest.approx(total)
        assert body["canonical_consistent"] is True


def test_same_size_horse_replacement_invalidates_run(session):
    seed_model(session)
    run = seed_race(session, race_id=_RACE, horses=_horses([0.4, 0.3, 0.2, 0.1]))
    session.get(RaceHorse, (_RACE, "H4")).entry_status = EntryStatus.CANCELLED
    session.add(Horse(horse_id="H5"))
    session.flush()
    session.add(RaceHorse(race_id=_RACE, horse_id="H5", horse_number=5,
                          entry_status=EntryStatus.STARTED))
    session.commit()
    assert not prediction_population_matches(session, run_id=run, race_id=_RACE)
    assert select_prediction_run(session, _RACE) is None
    assert canonical_win_probs(session, run_id=run, race_id=_RACE) == {}


@pytest.mark.parametrize("missing_prediction", [False, True])
def test_nonempty_complete_population_required(session, missing_prediction):
    seed_model(session)
    horses = _horses([0.6, 0.4])
    if missing_prediction:
        del horses[2]["win"]
    else:
        for horse in horses.values():
            horse["status"] = EntryStatus.CANCELLED
    run = seed_race(session, race_id=_RACE, horses=horses)
    assert not prediction_population_matches(session, run_id=run, race_id=_RACE)
    assert select_prediction_run(session, _RACE) is None
    assert canonical_win_probs(session, run_id=run, race_id=_RACE) == {}


def test_run_id_cannot_be_used_for_another_race(session):
    seed_model(session)
    first = seed_race(session, race_id=_RACE, horses=_horses([0.6, 0.4]))
    other_race = "200806010102"
    seed_race(session, race_id=other_race, horses=_horses([0.6, 0.4]))
    assert not prediction_population_matches(session, run_id=first, race_id=other_race)
    assert canonical_win_probs(session, run_id=first, race_id=other_race) == {}


def test_stale_selected_run_does_not_fall_back_to_older_or_other_model(session):
    seed_model(session)
    seed_model(session, model_version="candidate", adoption=AdoptionStatus.CANDIDATE)
    old = seed_race(session, race_id=_RACE, horses=_horses([0.5, 0.3, 0.2]))
    stale = seed_race(session, race_id=_RACE, horses=_horses([0.4, 0.3, 0.2, 0.1]))
    fresh_field = _horses([0.5, 0.3, 0.2])
    fresh_field[4] = {"status": EntryStatus.CANCELLED}
    candidate = seed_race(session, race_id=_RACE, model_version="candidate", horses=fresh_field)
    session.get(PredictionRun, stale).computed_at = (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=1)
    )
    session.commit()
    assert prediction_population_matches(session, run_id=old, race_id=_RACE)
    assert prediction_population_matches(session, run_id=candidate, race_id=_RACE)
    assert select_prediction_run(session, _RACE) is None
    assert select_prediction_run(session, _RACE, "m-active") is None
    assert select_prediction_run(session, _RACE, "candidate").prediction_run_id == candidate
