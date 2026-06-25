"""T013 (US2): predictions — deterministic run + audit, joint by bet_type, empties (SC-002/SC-003)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntryStatus

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_HORSES = {
    1: {"win": 0.45, "odds": 2.0}, 2: {"win": 0.25, "odds": 3.5},
    3: {"win": 0.18, "odds": 6.0}, 4: {"win": 0.12, "odds": 9.0},
}


def test_predictions_with_audit(client, session):
    seed_model(session)
    run = seed_race(session, race_id=_RACE, horses=_HORSES)
    r = client.get(f"/api/v1/races/{_RACE}/predictions")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["prediction_run_id"] == str(run)
    assert body["run"]["model_version"] == "m-active" and "computed_at" in body["run"]
    assert len(body["horses"]) == 4 and body["joint"] is None  # no joint without bet_type
    fav = next(h for h in body["horses"] if h["horse_number"] == 1)
    assert abs(fav["win"] - 0.45) < 1e-6


def test_joint_top_k_by_bet_type(client, session):
    seed_model(session)
    seed_race(session, race_id=_RACE, horses=_HORSES)
    r = client.get(f"/api/v1/races/{_RACE}/predictions", params={"bet_type": "exacta", "top": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["joint_bet_type"] == "exacta" and body["joint_logic_version"]
    assert len(body["joint"]) == 3
    probs = [e["prob"] for e in body["joint"]]
    assert probs == sorted(probs, reverse=True)  # ordered by -prob
    assert all(len(e["selection"]) == 2 for e in body["joint"])  # exacta = 2 horse numbers


def test_canonical_excludes_scratched_in_joint(client, session):
    seed_model(session)
    horses = dict(_HORSES)
    horses[4] = {"win": 0.12, "odds": 9.0, "status": EntryStatus.CANCELLED}  # scratched
    seed_race(session, race_id=_RACE, horses=horses)
    r = client.get(f"/api/v1/races/{_RACE}/predictions", params={"bet_type": "exacta", "top": 50})
    body = r.json()
    # scratched horse 4 never appears in any exacta selection
    assert all(4 not in e["selection"] for e in body["joint"])


def test_no_predictions_is_typed_empty(client, session):
    # race exists but no prediction_run
    import datetime

    from horseracing_db.models import Race
    session.merge(Race(race_id="200806010109", race_number=9,
                       race_date=datetime.date(2008, 6, 1), venue_code="05"))
    session.commit()
    r = client.get("/api/v1/races/200806010109/predictions")
    assert r.status_code == 200
    body = r.json()
    assert body["run"] is None and body["horses"] == []


def test_race_not_found_404(client):
    assert client.get("/api/v1/races/200806019999/predictions").status_code == 404
