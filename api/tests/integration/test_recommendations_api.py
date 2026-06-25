"""T017 (US4): recommendations SELECT-only, double-pseudo, GET does NOT write (SC-005)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation
from sqlalchemy import func, select

from tests._synth import add_recommendation, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_HORSES = {1: {"win": 0.5, "odds": 2.0}, 2: {"win": 0.5, "odds": 3.0}}


def test_recommendations_double_pseudo_and_read_only(client, session):
    seed_model(session)
    run = seed_race(session, race_id=_RACE, horses=_HORSES)
    add_recommendation(session, race_id=_RACE, run_id=run, bet_type=BetType.EXACTA,
                       selection=(1, 2), is_estimated=True)
    before = session.scalar(select(func.count()).select_from(Recommendation))

    r = client.get(f"/api/v1/races/{_RACE}/recommendations")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["bet_type"] == "exacta" and row["selection"] == [1, 2]
    assert row["is_estimated_odds"] is True and row["double_pseudo"] is True
    assert row["pseudo_roi"] is not None

    after = session.scalar(select(func.count()).select_from(Recommendation))
    assert after == before  # GET performed NO write (read-only)


def test_win_recommendations_excluded(client, session):
    seed_model(session)
    run = seed_race(session, race_id=_RACE, horses=_HORSES)
    # a win recommendation has a dict selection — must be excluded from this endpoint
    session.add(Recommendation(prediction_run_id=run, race_id=_RACE, bet_type=BetType.WIN,
                               selection={"horse_id": "H1", "horse_number": 1},
                               is_estimated_odds=False, logic_version="win-lv"))
    session.commit()
    body = client.get(f"/api/v1/races/{_RACE}/recommendations").json()
    assert body["items"] == []  # win excluded (exotic-only)


def test_no_recommendations_typed_empty(client, session):
    seed_model(session)
    seed_race(session, race_id=_RACE, horses=_HORSES)
    body = client.get(f"/api/v1/races/{_RACE}/recommendations").json()
    assert body["items"] == []


def test_race_not_found_404(client):
    assert client.get("/api/v1/races/200806019999/recommendations").status_code == 404
