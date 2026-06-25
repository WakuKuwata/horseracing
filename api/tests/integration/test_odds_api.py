"""T015 (US3): odds — real vs estimated in separate fields, labels, typed-empty (SC-004)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import BetType

from tests._synth import add_exotic_odds, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_HORSES = {
    1: {"win": 0.45, "odds": 2.0}, 2: {"win": 0.25, "odds": 3.5},
    3: {"win": 0.18, "odds": 6.0}, 4: {"win": 0.12, "odds": 9.0},
}


def test_real_estimated_real_exotic_separated(client, session):
    seed_model(session)
    seed_race(session, race_id=_RACE, horses=_HORSES)
    add_exotic_odds(session, race_id=_RACE, bet_type=BetType.TRIO, selection=[1, 2, 3], odds=40.0)

    r = client.get(f"/api/v1/races/{_RACE}/odds", params={"bet_type": "exacta", "top": 5})
    assert r.status_code == 200
    body = r.json()

    # win: real, labeled, never estimated
    assert body["win"] and all(w["odds_source"] == "real" and w["is_estimated"] is False
                               for w in body["win"])
    # estimated: pseudo, labeled, as_of present; includes win estimated + exacta top-K
    assert body["estimated"] and all(e["odds_source"] == "estimated" and e["is_estimated"] is True
                                     and e["pseudo"] is True and "as_of" in e
                                     for e in body["estimated"])
    assert any(e["bet_type"] == "exacta" for e in body["estimated"])
    # real_exotic: real dividend with coverage + updated_at
    assert body["real_exotic"] and body["real_exotic"][0]["odds_source"] == "real"
    assert body["real_exotic"][0]["coverage_scope"] == "partial"
    assert body["real_exotic"][0]["is_estimated"] is False


def test_no_odds_is_typed_empty_not_500(client, session):
    # race with horses but no win odds -> estimated empty (MarketOddsError caught), 200 (never 500)
    seed_model(session)
    seed_race(session, race_id=_RACE, horses={1: {"win": 0.5}, 2: {"win": 0.5}})  # no odds
    r = client.get(f"/api/v1/races/{_RACE}/odds")
    assert r.status_code == 200
    body = r.json()
    # started horses still listed (odds null); estimated/real_exotic are empty (no usable odds)
    assert all(w["odds"] is None for w in body["win"])
    assert body["estimated"] == [] and body["real_exotic"] == []


def test_race_not_found_404(client):
    assert client.get("/api/v1/races/200806019999/odds").status_code == 404
