"""T010 (US1): walk-forward p→p' calibration eval over a DB period (Feature 017).

Covers SC-001/SC-002/SC-003/SC-005. Seeds predicted races with a fixed prob vector and a winner
distribution that makes the favorite win MORE often than p says (model under-confident) so γ>1 helps,
then evaluates calibrate-eval walk-forward.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.model_calibration import evaluate_calibration_db, load_p_samples
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration

_P = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}


def _seed_window(session, year, n):
    for r in range(1, n + 1):
        rid = f"{year}0503{r:02d}01"
        # favorite A wins 70% (under-confident model at top); B/C/D otherwise — deterministic.
        if r % 10 < 7:
            winner = "A"
        elif r % 10 < 9:
            winner = "B"
        else:
            winner = "C"
        finish = {"A": 1 if winner == "A" else 2, "B": 1 if winner == "B" else 3,
                  "C": 1 if winner == "C" else 4, "D": 5}
        seed_predicted_race(session, race_id=rid, win_probs=_P, finish=finish,
                            race_date=datetime.date(year, 5, 3))


def test_calibrate_eval_walk_forward(session):
    _seed_window(session, 2008, 60)
    cal, rep, joint = evaluate_calibration_db(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        method="power", min_races=10, min_wins=5, train_frac=0.5,
    )
    # calibrator fit on the earlier half; metrics computed on the strictly-later half
    assert cal.method == "power" and cal.sufficient
    assert rep.n_races > 0
    # both raw and calibrated metrics present
    assert rep.nll_p > 0 and rep.nll_pp > 0
    assert rep.brier_p > 0 and rep.brier_pp > 0
    # 009-after joint reliability reported per bet type with a non-degradation flag (SC-005)
    assert set(joint) == {"exacta", "trifecta"}
    for jr in joint.values():
        assert jr.n_races > 0
        assert isinstance(jr.not_degraded, bool)


def test_calibrate_eval_deterministic(session):
    _seed_window(session, 2008, 60)
    a = evaluate_calibration_db(session, date_from=datetime.date(2008, 1, 1),
                                date_to=datetime.date(2008, 12, 31), min_races=10, min_wins=5)
    b = evaluate_calibration_db(session, date_from=datetime.date(2008, 1, 1),
                                date_to=datetime.date(2008, 12, 31), min_races=10, min_wins=5)
    assert a[0].params == b[0].params                      # same gamma (SC-003)
    assert (a[1].nll_pp, a[1].brier_pp, a[1].ece_pp) == (b[1].nll_pp, b[1].brier_pp, b[1].ece_pp)


def test_calibrator_excludes_dead_heat(session):
    # one dead-heat race (two horses finish 1st) must be excluded from samples' winner signal
    seed_predicted_race(session, race_id="200805039901",
                        win_probs=_P, finish={"A": 1, "B": 1, "C": 3, "D": 4},
                        race_date=datetime.date(2008, 5, 3))
    samples = load_p_samples(session, date_from=datetime.date(2008, 1, 1),
                             date_to=datetime.date(2008, 12, 31))
    dh = [s for s in samples if s[0] == "200805039901"][0]
    assert dh[3] is None and dh[4] is True                 # winner None, dead_heat True
