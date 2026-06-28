"""T019 (US3): prior_starts_band is a leak-safe, NEUTRAL prior-start-count fact (few/some/many).

codex reframed US3 from a calibration-trust signal to a factual history-volume hint. Computed from
STARTED entries strictly BEFORE the target race date — never from results or odds. Races after the
target don't count.
"""

from __future__ import annotations

import datetime

import pytest

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_TARGET = "200806100101"


def _seed_prior(session, *, horse_numbers, day):
    """Seed a prior race (date 2008-05-<day>) where the given horse_numbers STARTED."""
    rid = f"200805{day:02d}{day:02d}01"
    seed_race(
        session, race_id=rid, race_date=datetime.date(2008, 5, day),
        horses={n: {"win": 0.5, "odds": 2.0} for n in horse_numbers},
    )


def test_data_backing_buckets(client, session):
    seed_model(session)
    # prior starts before target: H1 x6 (strong), H2 x1 (weak), H3 x3 (medium)
    for day in range(1, 7):
        _seed_prior(session, horse_numbers=[1], day=day)
    _seed_prior(session, horse_numbers=[2], day=7)
    for day in range(8, 11):
        _seed_prior(session, horse_numbers=[3], day=day)
    # a race AFTER the target must NOT count toward backing (leak-safe)
    seed_race(session, race_id="200807010101", race_date=datetime.date(2008, 7, 1),
              horses={2: {"win": 0.5, "odds": 2.0}})

    seed_race(session, race_id=_TARGET, race_date=datetime.date(2008, 6, 10),
              horses={1: {"win": 0.4, "odds": 2.0}, 2: {"win": 0.3, "odds": 3.0},
                      3: {"win": 0.3, "odds": 4.0}})

    body = client.get(f"/api/v1/races/{_TARGET}/predictions").json()
    band = {h["horse_number"]: h["prior_starts_band"] for h in body["horses"]}
    assert band[1] == "many"   # 6 prior starts
    assert band[2] == "few"    # 1 prior start (the post-target race doesn't count)
    assert band[3] == "some"   # 3 prior starts


def test_debut_horse_is_few_without_results_or_odds(client, session):
    seed_model(session)
    # target horse with NO prior starts and NO odds -> few, computed without results/odds
    seed_race(session, race_id=_TARGET, race_date=datetime.date(2008, 6, 10),
              horses={1: {"win": 0.5}})  # no odds, no finish
    body = client.get(f"/api/v1/races/{_TARGET}/predictions").json()
    h1 = next(h for h in body["horses"] if h["horse_number"] == 1)
    assert h1["prior_starts_band"] == "few"
