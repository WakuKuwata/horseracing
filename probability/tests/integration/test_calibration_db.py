"""US2 (SC-005/007): evaluate_calibration over DB races returns PL + baseline reports."""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.calibration import evaluate_calibration
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration


def test_calibration_reports_pl_and_baseline(session):
    # two races; realized order follows the favorites (PL-favored)
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    seed_predicted_race(session, race_id="200806010102",
                        win_probs={"H1": 0.45, "H2": 0.35, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})

    reports = evaluate_calibration(
        session, start_date=datetime.date(2008, 6, 1), end_date=datetime.date(2008, 6, 1),
        bet_type="exacta",
    )
    assert set(reports) == {"plackett_luce", "independent_product"}
    pl, ind = reports["plackett_luce"], reports["independent_product"]
    assert pl.n_races == 2 and ind.n_races == 2
    # realized = favorite order -> PL not worse than the naive independent product
    assert pl.nll <= ind.nll + 1e-9


def test_dead_heat_or_missing_results_skipped(session):
    # a dead heat for 1st -> no unique ordered combo -> race excluded from samples
    seed_predicted_race(session, race_id="200806010103",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 1, "H3": 3})  # dead heat at 1st
    reports = evaluate_calibration(
        session, start_date=datetime.date(2008, 6, 1), end_date=datetime.date(2008, 6, 1),
        bet_type="exacta",
    )
    assert reports["plackett_luce"].n_races == 0
