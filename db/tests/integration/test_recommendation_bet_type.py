"""US4 / FR-021, FR-022: bet_type CHECK (7 券種) and estimated-odds distinction."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation

from ._prediction_helpers import RACE_ID, setup_run

pytestmark = pytest.mark.integration


def test_all_seven_bet_types_accepted(session):
    run = setup_run(session)
    assert len(BetType.ALL) == 7
    for i, bt in enumerate(BetType.ALL):
        session.add(Recommendation(
            prediction_run_id=run.prediction_run_id, race_id=RACE_ID,
            bet_type=bt, selection={"i": i}, logic_version="v1",
        ))
    session.flush()  # no error


def test_invalid_bet_type_rejected(session):
    run = setup_run(session)
    session.add(Recommendation(
        prediction_run_id=run.prediction_run_id, race_id=RACE_ID,
        bet_type="bracket_quinella", selection={}, logic_version="v1",
    ))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_estimated_vs_real_odds_distinguished(session):
    run = setup_run(session)
    session.add_all([
        Recommendation(prediction_run_id=run.prediction_run_id, race_id=RACE_ID,
                       bet_type=BetType.WIN, selection={"n": 1}, logic_version="v1",
                       market_odds_used=Decimal("3.0"), is_estimated_odds=False),
        Recommendation(prediction_run_id=run.prediction_run_id, race_id=RACE_ID,
                       bet_type=BetType.PLACE, selection={"n": 1}, logic_version="v1",
                       estimated_market_odds_used=Decimal("1.4"), is_estimated_odds=True),
    ])
    session.flush()

    rows = session.query(Recommendation).filter_by(prediction_run_id=run.prediction_run_id).all()
    estimated = [r for r in rows if r.is_estimated_odds]
    actual = [r for r in rows if not r.is_estimated_odds]
    assert len(estimated) == 1 and estimated[0].estimated_market_odds_used == Decimal("1.4")
    assert len(actual) == 1 and actual[0].market_odds_used == Decimal("3.0")
