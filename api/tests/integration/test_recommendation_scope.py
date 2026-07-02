"""Feature 043: recommendations are scoped to the displayed prediction_run (no append-only /
older-run duplication) and expose stake_fraction + recommendation_id. Read-only.
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import PredictionRun

from horseracing_api.selection import select_prediction_run
from tests._synth import add_recommendation, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010111"


def test_recommendations_scoped_to_selected_run_with_stake(client, session):
    seed_model(session)
    run_a = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 2.0}, 2: {"win": 0.3, "odds": 3.0},
    })
    # a SECOND run for the same race (its recs must NOT appear if it isn't the selected one)
    other = PredictionRun(race_id=_RACE, model_version="m-active", logic_version="other")
    session.add(other)
    session.flush()
    other_id = other.prediction_run_id
    session.commit()

    # whichever run the read API deterministically selects gets the (stake-bearing) rec;
    # the other run gets a TRIO rec that must be excluded from the response.
    selected = select_prediction_run(session, _RACE).prediction_run_id
    not_selected = other_id if selected == run_a else run_a
    add_recommendation(session, race_id=_RACE, run_id=not_selected,
                       bet_type=BetType.TRIO, selection=(3, 4, 5))
    add_recommendation(session, race_id=_RACE, run_id=selected,
                       bet_type=BetType.EXACTA, selection=(1, 2), stake_fraction=0.0123)

    items = client.get(f"/api/v1/races/{_RACE}/recommendations").json()["items"]
    assert {i["prediction_run_id"] for i in items} == {str(selected)}  # only selected run
    assert all(i["bet_type"] != "trio" for i in items)                  # older run excluded
    row = items[0]
    assert row["recommendation_id"] and row["stake_fraction"] == 0.0123


def test_recommendations_empty_when_no_run(client, session):
    seed_model(session)
    # race exists but no prediction_run → typed-empty (not an error), no stray rows
    from horseracing_db.models import Race
    session.merge(Race(race_id="200806010112", race_number=1))
    session.commit()
    body = client.get("/api/v1/races/200806010112/recommendations").json()
    assert body["items"] == []
