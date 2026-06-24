"""US2 (SC-004/007): evaluate_market_odds over DB races; pseudo; reads odds+results, not p."""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.market_calibration import evaluate_market_odds
from tests._synth import seed_odds_race

pytestmark = pytest.mark.integration


def test_market_validation_reports(session):
    # odds chosen so R·S=1 (R_win=0.8) -> recovery error ~0
    seed_odds_race(session, race_id="200806010101",
                   win_odds={"H1": 1.6, "H2": 3.2, "H3": 3.2}, finish={"H1": 1, "H2": 2, "H3": 3})
    seed_odds_race(session, race_id="200806010102",
                   win_odds={"H1": 2.0, "H2": 4.0, "H3": 4.0}, finish={"H1": 1, "H2": 2, "H3": 3})

    rec, qcal = evaluate_market_odds(
        session, start_date=datetime.date(2008, 6, 1), end_date=datetime.date(2008, 6, 1)
    )
    assert rec.n_races == 2 and qcal.n_races == 2
    assert rec.pseudo and qcal.pseudo                 # pseudo evaluation
    assert rec.mean_abs_log_ratio < 0.2               # near-recovery for these odds
    assert qcal.nll > 0
