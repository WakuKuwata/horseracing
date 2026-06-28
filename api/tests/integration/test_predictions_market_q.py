"""T008 (US1): predictions expose market q on the SAME canonical field as model p (FR-001/004).

p (win) and q (market_win_prob) are SEPARATE fields; q is the renormalized vote-share over the
canonical field; null (never 0) when a horse lacks valid odds; canonical_consistent reflects whether
p and q share the population (R1). Endpoint is read-only (GET).
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntryStatus

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_HORSES = {
    1: {"win": 0.45, "odds": 2.0, "finish": 1}, 2: {"win": 0.25, "odds": 3.5, "finish": 2},
    3: {"win": 0.18, "odds": 6.0, "finish": 3}, 4: {"win": 0.12, "odds": 9.0, "finish": 4},
}


def test_p_and_q_on_same_canonical_field(client, session):
    seed_model(session)
    seed_race(session, race_id=_RACE, horses=_HORSES)
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()

    assert body["market_prob_source"] == "win_odds_vote_share"
    assert body["canonical_consistent"] is True
    assert body["odds_source"] == "final"  # results present
    assert body["odds_as_of"] is not None

    qs = {h["horse_number"]: h["market_win_prob"] for h in body["horses"]}
    assert all(v is not None for v in qs.values())          # all have odds
    assert abs(sum(qs.values()) - 1.0) < 1e-6               # q renormalized on the field (Σq≈1)
    # p and q are SEPARATE values (favorite by odds gets the largest q, but q != p)
    fav = next(h for h in body["horses"] if h["horse_number"] == 1)
    assert fav["market_win_prob"] != fav["win"]
    assert qs[1] == max(qs.values())                        # lowest odds -> highest q


def test_missing_odds_q_is_null_and_inconsistent(client, session):
    seed_model(session)
    horses = dict(_HORSES)
    horses[4] = {"win": 0.12, "odds": None, "finish": 4}    # has p, no valid odds
    seed_race(session, race_id=_RACE, horses=horses)
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()

    h4 = next(h for h in body["horses"] if h["horse_number"] == 4)
    assert h4["market_win_prob"] is None                    # null, never 0-filled (FR-004)
    assert h4["win"] is not None                            # p still shown
    # q population {1,2,3} != p population {1,2,3,4} -> divergence must be suppressed (R1)
    assert body["canonical_consistent"] is False


def test_odds_source_prerace_when_no_results(client, session):
    seed_model(session)
    horses = {n: {k: v for k, v in h.items() if k != "finish"} for n, h in _HORSES.items()}
    seed_race(session, race_id=_RACE, horses=horses)  # no finish -> no results
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    assert body["odds_source"] == "prerace"


def test_scratched_excluded_from_q_field(client, session):
    seed_model(session)
    horses = dict(_HORSES)
    horses[4] = {"win": 0.12, "odds": 9.0, "status": EntryStatus.CANCELLED}
    seed_race(session, race_id=_RACE, horses=horses)
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    numbers = {h["horse_number"] for h in body["horses"]}
    assert 4 not in numbers  # scratched not a started horse
    qs = [h["market_win_prob"] for h in body["horses"]]
    assert abs(sum(qs) - 1.0) < 1e-6  # q renormalized over remaining started field
