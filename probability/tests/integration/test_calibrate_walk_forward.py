"""T022: calibrator walk-forward leak boundary + small-data fallback (Feature 017, SC-002)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import RaceResult
from sqlalchemy import update

from horseracing_probability.model_calibration import (
    evaluate_calibration_db,
    fit_p_calibrator,
    load_p_samples,
    split_before,
)
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration

_P = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}


def _seed(session, year, n):
    for r in range(1, n + 1):
        rid = f"{year}0503{r:02d}01"
        winner = "A" if r % 10 < 7 else ("B" if r % 10 < 9 else "C")
        finish = {"A": 1 if winner == "A" else 2, "B": 1 if winner == "B" else 3,
                  "C": 1 if winner == "C" else 4, "D": 5}
        seed_predicted_race(session, race_id=rid, win_probs=_P, finish=finish,
                            race_date=datetime.date(year, 5, 3))


def test_fit_uses_only_strictly_prior_races(session):
    _seed(session, 2007, 40)   # train window
    _seed(session, 2008, 20)   # eval window (strictly after)
    samples = load_p_samples(session, date_from=datetime.date(2007, 1, 1),
                             date_to=datetime.date(2008, 12, 31))
    # split before the first 2008 race → only 2007 samples feed the fit
    train = split_before(samples, datetime.date(2008, 5, 3), "200805030101")
    assert train and all(s[1].year == 2007 for s in train)
    cal_before = fit_p_calibrator([(p, w) for (_r, _d, p, w, _dh) in train], min_races=10,
                                  min_wins=5)

    # mutate a 2008 (eval) result; the calibrator fit on 2007 must be unchanged (no leak)
    rid_2008 = "200805030101"
    session.execute(update(RaceResult).where(RaceResult.race_id == rid_2008)
                    .values(finish_order=99))
    session.commit()
    samples2 = load_p_samples(session, date_from=datetime.date(2007, 1, 1),
                              date_to=datetime.date(2008, 12, 31))
    train2 = split_before(samples2, datetime.date(2008, 5, 3), "200805030101")
    cal_after = fit_p_calibrator([(p, w) for (_r, _d, p, w, _dh) in train2], min_races=10,
                                 min_wins=5)
    assert cal_before.params == cal_after.params   # eval-window mutation did not change the fit


def test_small_window_falls_back_to_identity(session):
    _seed(session, 2008, 8)   # too few races
    cal, _rep, _joint = evaluate_calibration_db(
        session, date_from=datetime.date(2008, 1, 1), date_to=datetime.date(2008, 12, 31),
        min_races=50, min_wins=30, train_frac=0.5,
    )
    assert cal.sufficient is False and cal.method == "identity"
