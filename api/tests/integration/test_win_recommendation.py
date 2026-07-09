"""Feature 045: win rows are returned with selection normalised to [horse_number];
win rows without a horse_number are dropped; real-odds fields intact; run scoping holds.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation

from tests._synth import add_recommendation, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010111"


def _add_win(session, *, race_id, run_id, selection, stake=None):
    session.add(Recommendation(
        prediction_run_id=run_id, race_id=race_id, bet_type=BetType.WIN,
        selection=selection,
        market_odds_used=Decimal("4.2"), estimated_market_odds_used=None,
        is_estimated_odds=False, pseudo_odds=Decimal("3.1"), pseudo_roi=Decimal("0.35"),
        stake_fraction=(Decimal(str(stake)) if stake is not None else None),
        logic_version="win-lv",
    ))
    session.commit()


def test_win_row_returned_with_normalised_selection(client, session):
    seed_model(session)
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 2.0}, 2: {"win": 0.3, "odds": 3.0},
    })
    _add_win(session, race_id=_RACE, run_id=run_id,
             selection={"horse_id": "H1", "horse_number": 1}, stake=0.02)
    add_recommendation(session, race_id=_RACE, run_id=run_id,
                       bet_type=BetType.EXACTA, selection=(1, 2))

    items = client.get(f"/api/v1/races/{_RACE}/recommendations").json()["items"]
    win = [i for i in items if i["bet_type"] == "win"]
    assert len(win) == 1
    w = win[0]
    assert w["selection"] == [1]                    # dict → [horse_number]
    assert w["is_estimated_odds"] is False          # real win odds
    assert w["market_odds_used"] == 4.2 and w["estimated_market_odds_used"] is None
    assert w["double_pseudo"] is False              # pseudo-ROI is single-pseudo for win
    assert w["stake_fraction"] == 0.02              # Kelly stake exposed (045)
    # exotic row still present alongside
    assert any(i["bet_type"] == "exacta" for i in items)


def test_win_row_without_horse_number_is_dropped(client, session):
    seed_model(session)
    run_id = seed_race(session, race_id=_RACE, horses={1: {"win": 0.4, "odds": 2.0}})
    _add_win(session, race_id=_RACE, run_id=run_id,
             selection={"horse_id": "H9", "horse_number": None})
    items = client.get(f"/api/v1/races/{_RACE}/recommendations").json()["items"]
    assert all(i["bet_type"] != "win" for i in items)  # undisplayable row excluded, no 500


def test_win_policy_status_and_favorite_baseline(client, session):
    """Feature 064: win_policy_status distinguishes an empty win section; favorite_baseline is the
    read-time market reference (favorite = lowest odds)."""
    seed_model(session)
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 2.0}, 2: {"win": 0.3, "odds": 3.0},
    })
    # run exists, only an exotic rec (win policy selected nothing) → no_win_selected
    add_recommendation(session, race_id=_RACE, run_id=run_id,
                       bet_type=BetType.EXACTA, selection=(1, 2))
    body = client.get(f"/api/v1/races/{_RACE}/recommendations").json()
    assert body["win_policy_status"] == "no_win_selected"
    assert body["favorite_baseline"]["horse_number"] == 1     # lowest odds (2.0)
    assert body["favorite_baseline"]["odds"] == 2.0

    # add a win rec → generated
    _add_win(session, race_id=_RACE, run_id=run_id,
             selection={"horse_id": "H1", "horse_number": 1}, stake=0.02)
    body2 = client.get(f"/api/v1/races/{_RACE}/recommendations").json()
    assert body2["win_policy_status"] == "generated"
