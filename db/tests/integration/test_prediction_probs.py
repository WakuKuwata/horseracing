"""US4 / FR-019 / 憲法 IV: race_predictions monotonic probability CHECK."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from horseracing_db.models import RacePrediction

from ._prediction_helpers import setup_run

pytestmark = pytest.mark.integration


def test_valid_monotonic_probs_accepted(session):
    run = setup_run(session)
    session.add(RacePrediction(prediction_run_id=run.prediction_run_id, horse_id="H1",
                               win_prob=0.2, top2_prob=0.4, top3_prob=0.6))
    session.flush()  # no error


@pytest.mark.parametrize(
    "win,top2,top3",
    [
        (0.5, 0.4, 0.6),   # win > top2
        (0.2, 0.7, 0.6),   # top2 > top3
        (-0.1, 0.4, 0.6),  # win < 0
        (0.2, 0.4, 1.2),   # top3 > 1
    ],
)
def test_invalid_probs_rejected(session, win, top2, top3):
    run = setup_run(session)
    session.add(RacePrediction(prediction_run_id=run.prediction_run_id, horse_id="H1",
                               win_prob=win, top2_prob=top2, top3_prob=top3))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
