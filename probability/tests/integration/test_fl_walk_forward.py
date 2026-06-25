"""T011 (013): walk-forward sample loading + fit, strictly-before (race_date, race_id) (SC-001/002)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

from horseracing_probability.fl_bias import fit_fl_calibrator, load_samples, race_before

pytestmark = pytest.mark.integration


def _seed_race(session, race_id, race_date, odds_by_horse, winner):
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]), race_date=race_date,
                       venue_code=race_id[4:6]))
    for hid in odds_by_horse:
        session.merge(Horse(horse_id=hid, horse_name=hid))
    session.flush()
    for i, (hid, o) in enumerate(odds_by_horse.items(), start=1):
        session.add(RaceHorse(race_id=race_id, horse_id=hid, horse_number=i,
                              odds=Decimal(str(o)), entry_status=EntryStatus.STARTED))
        session.add(RaceResult(race_id=race_id, horse_id=hid,
                               finish_order=1 if hid == winner else i + 1,
                               result_status=ResultStatus.FINISHED))
    session.commit()


def test_load_samples_window_bounded_and_ordered(session):
    odds = {"A": 2.0, "B": 4.0, "C": 8.0}
    # 2007 train races + a 2008 eval race
    _seed_race(session, "200701010101", datetime.date(2007, 4, 1), odds, "A")
    _seed_race(session, "200701010102", datetime.date(2007, 4, 1), odds, "B")
    _seed_race(session, "200705030111", datetime.date(2007, 9, 1), odds, "A")
    _seed_race(session, "200801010101", datetime.date(2008, 4, 1), odds, "A")

    train = load_samples(session, date_from=datetime.date(2007, 1, 1),
                         date_to=datetime.date(2007, 12, 31))
    ids = [r[0] for r in train]
    assert ids == ["200701010101", "200701010102", "200705030111"]  # ordered, 2008 excluded

    # samples shape: (race_id, race_date, win_odds, winner)
    cal = fit_fl_calibrator([(wo, w) for _, _, wo, w in train],
                            train_window=(datetime.date(2007, 1, 1), datetime.date(2007, 12, 31)))
    assert cal.n_races == 3
    assert cal.train_window[1].year == 2007  # never sees 2008 results (walk-forward)


def test_race_before_lexicographic():
    d07 = datetime.date(2007, 4, 1)
    d08 = datetime.date(2008, 4, 1)
    assert race_before(d07, "200701010102", d08, "200801010101") is True
    # same date -> race_id tie-break (deterministic, never date-level <=)
    assert race_before(d07, "200701010101", d07, "200701010102") is True
    assert race_before(d07, "200701010102", d07, "200701010101") is False
