"""Feature 049: the recommendations endpoint returns retrospective WIN backtest fields
(settled/hit/dead_heat/counterfactual_snapshot_gross_return/net_return) — real odds, win-only, unsettled → null.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation

from tests._synth import add_recommendation, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010111"


def _add_win(session, *, race_id, run_id, horse_number, odds="4.2", stake=None):
    session.add(Recommendation(
        prediction_run_id=run_id, race_id=race_id, bet_type=BetType.WIN,
        selection={"horse_id": f"H{horse_number}", "horse_number": horse_number},
        market_odds_used=Decimal(str(odds)), estimated_market_odds_used=None,
        is_estimated_odds=False, pseudo_odds=Decimal("3.1"), pseudo_roi=Decimal("0.35"),
        stake_fraction=(Decimal(str(stake)) if stake is not None else None),
        logic_version="win-lv",
    ))
    session.commit()


def _win_row(client, race_id, horse_number):
    items = client.get(f"/api/v1/races/{race_id}/recommendations").json()["items"]
    hits = [i for i in items if i["bet_type"] == "win" and i["selection"] == [horse_number]]
    assert len(hits) == 1
    return hits[0]


def test_win_hit_reports_real_realized_return(client, session):
    seed_model(session)
    # horse 1 wins (finish 1), odds 4.2 → realized 4.2 / roi 3.2
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 4.2, "finish": 1},
        2: {"win": 0.3, "odds": 3.0, "finish": 2},
    })
    _add_win(session, race_id=_RACE, run_id=run_id, horse_number=1, odds="4.2", stake=0.02)
    w = _win_row(client, _RACE, 1)
    assert w["settled"] is True and w["hit"] is True and w["dead_heat"] is False
    assert w["counterfactual_snapshot_gross_return"] == 4.2 and w["counterfactual_snapshot_net_return"] == pytest.approx(3.2)


def test_win_miss_is_minus_one(client, session):
    seed_model(session)
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 4.2, "finish": 5},
        2: {"win": 0.3, "odds": 3.0, "finish": 1},
    })
    _add_win(session, race_id=_RACE, run_id=run_id, horse_number=1)
    w = _win_row(client, _RACE, 1)
    assert w["settled"] is True and w["hit"] is False
    assert w["counterfactual_snapshot_gross_return"] == 0.0 and w["counterfactual_snapshot_net_return"] == -1.0


def test_win_dead_heat_flagged(client, session):
    seed_model(session)
    # both horse 1 and 2 finish 1st (dead heat)
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 4.2, "finish": 1},
        2: {"win": 0.3, "odds": 3.0, "finish": 1},
    })
    _add_win(session, race_id=_RACE, run_id=run_id, horse_number=1, odds="4.2")
    w = _win_row(client, _RACE, 1)
    assert w["hit"] is True and w["dead_heat"] is True and w["counterfactual_snapshot_gross_return"] == 4.2


def test_win_unsettled_is_null(client, session):
    seed_model(session)
    # no "finish" → no result rows → unsettled
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 4.2}, 2: {"win": 0.3, "odds": 3.0},
    })
    _add_win(session, race_id=_RACE, run_id=run_id, horse_number=1)
    w = _win_row(client, _RACE, 1)
    assert w["settled"] is False and w["hit"] is None
    assert w["counterfactual_snapshot_gross_return"] is None and w["counterfactual_snapshot_net_return"] is None


def test_win_void_when_horse_has_no_result(client, session):
    seed_model(session)
    # race IS settled (horse 2 finished) but horse 1 was scratched post-rec → no result row
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 4.2}, 2: {"win": 0.3, "odds": 3.0, "finish": 1},
    })
    # remove any auto result for H1 (seed_race only adds results for horses with "finish")
    _add_win(session, race_id=_RACE, run_id=run_id, horse_number=1)
    w = _win_row(client, _RACE, 1)
    assert w["settled"] is True and w["hit"] is None  # void, not a loss
    assert w["counterfactual_snapshot_gross_return"] is None


def test_exotic_row_has_no_realized_fields(client, session):
    seed_model(session)
    run_id = seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 4.2, "finish": 1}, 2: {"win": 0.3, "odds": 3.0, "finish": 2},
    })
    add_recommendation(session, race_id=_RACE, run_id=run_id,
                       bet_type=BetType.EXACTA, selection=(1, 2))
    items = client.get(f"/api/v1/races/{_RACE}/recommendations").json()["items"]
    ex = [i for i in items if i["bet_type"] == "exacta"][0]
    # exotic uses estimated odds → realised valuation is win-only; stays null (settled False)
    assert ex["settled"] is False and ex["hit"] is None and ex["counterfactual_snapshot_net_return"] is None
