"""T019 (013): walk-forward fit(2007) -> evaluate(2008), q vs q' calibration (SC-004/SC-002)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

from horseracing_probability.fl_bias import fit_fl_calibrator, load_samples
from horseracing_probability.market_calibration import evaluate_q_vs_qprime

pytestmark = pytest.mark.integration

ODDS = {"A": 2.0, "B": 4.0, "C": 7.0, "D": 15.0}  # A is the favorite


def _seed(session, race_id, race_date, winner):
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
                       venue_code=race_id[4:6]))
    for hid in ODDS:
        session.merge(Horse(horse_id=hid, horse_name=hid))
    session.flush()
    for i, (hid, o) in enumerate(ODDS.items(), start=1):
        session.add(RaceHorse(race_id=race_id, horse_id=hid, horse_number=i,
                              odds=Decimal(str(o)), entry_status=EntryStatus.STARTED))
        session.add(RaceResult(race_id=race_id, horse_id=hid,
                               finish_order=1 if hid == winner else i + 1,
                               result_status=ResultStatus.FINISHED))
    session.commit()


def _seed_window(session, year, n, fav_share=4):
    # favorite A wins (n - n//fav_share) of the races -> market under-rates A -> γ>1 helps
    for r in range(1, n + 1):
        rid = f"{year}05030{r:02d}1"[:12].ljust(12, "0")
        rid = f"{year}0503{r:02d}01"
        winner = "A" if r % fav_share != 0 else "B"
        _seed(session, rid, datetime.date(year, 5, 3), winner)


def test_walk_forward_fit_then_eval(session):
    _seed_window(session, 2007, 40)   # train
    _seed_window(session, 2008, 20)    # eval (strictly after)

    train = load_samples(session, date_from=datetime.date(2007, 1, 1),
                         date_to=datetime.date(2007, 12, 31))
    cal = fit_fl_calibrator([(wo, w) for _, _, wo, w in train],
                            train_window=(datetime.date(2007, 1, 1), datetime.date(2007, 12, 31)))
    assert cal.n_races == 40

    eval_s = load_samples(session, date_from=datetime.date(2008, 1, 1),
                          date_to=datetime.date(2008, 12, 31))
    rep = evaluate_q_vs_qprime([(wo, w) for _, _, wo, w in eval_s], cal)
    assert rep.n_races == 20
    assert isinstance(rep.improved, bool)
    # determinism
    rep2 = evaluate_q_vs_qprime([(wo, w) for _, _, wo, w in eval_s], cal)
    assert rep.nll_qp == rep2.nll_qp and rep.ece_qp == rep2.ece_qp
