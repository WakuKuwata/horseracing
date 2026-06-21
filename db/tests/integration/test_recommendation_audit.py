"""US4 / SC-004: recommendation audit columns reconstruct the decision even after
the live odds are overwritten."""

from __future__ import annotations

from decimal import Decimal

import pytest

from horseracing_db.enums import BetType
from horseracing_db.models import RaceHorse, Recommendation

from ._prediction_helpers import RACE_ID, setup_run

pytestmark = pytest.mark.integration


def test_audit_columns_survive_odds_overwrite(session):
    run = setup_run(session, odds=3.0)
    rec = Recommendation(
        prediction_run_id=run.prediction_run_id,
        race_id=RACE_ID,
        bet_type=BetType.WIN,
        selection={"horse_number": 1},
        market_odds_used=Decimal("3.0"),
        is_estimated_odds=False,
        pseudo_odds=Decimal("2.5"),
        pseudo_roi=Decimal("0.2"),
        logic_version="bet-v1",
    )
    session.add(rec)
    session.commit()

    # Live odds are overwritten later (no snapshot history kept on race_horses).
    rh = session.get(RaceHorse, (RACE_ID, "H1"))
    rh.odds = Decimal("10.0")
    session.commit()

    session.refresh(rec)
    # The recommendation reconstructs the decision from its own audit copy, not the live odds.
    assert rec.market_odds_used == Decimal("3.0")
    assert rec.pseudo_odds == Decimal("2.5")
    assert rec.pseudo_roi == Decimal("0.2")
    assert rec.logic_version == "bet-v1"
    assert float(session.get(RaceHorse, (RACE_ID, "H1")).odds) == 10.0
