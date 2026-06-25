"""T022 (US4): fl-fit / fl-evaluate CLI — calibrator summary, adoption-gate output, overlap guard."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

from horseracing_probability.cli import main

pytestmark = pytest.mark.integration

ODDS = {"A": 2.0, "B": 4.0, "C": 7.0, "D": 15.0}


def _seed_window(session, year, n):
    for r in range(1, n + 1):
        rid = f"{year}0503{r:02d}01"
        winner = "A" if r % 4 != 0 else "B"
        session.merge(Race(race_id=rid, race_number=1, race_date=datetime.date(year, 5, 3),
                           venue_code="05"))
        for hid in ODDS:
            session.merge(Horse(horse_id=hid, horse_name=hid))
        session.flush()
        for i, (hid, o) in enumerate(ODDS.items(), start=1):
            session.add(RaceHorse(race_id=rid, horse_id=hid, horse_number=i,
                                  odds=Decimal(str(o)), entry_status=EntryStatus.STARTED))
            session.add(RaceResult(race_id=rid, horse_id=hid,
                                   finish_order=1 if hid == winner else i + 1,
                                   result_status=ResultStatus.FINISHED))
        session.commit()


def test_fl_fit_cli(session, tmp_path, capsys, database_url):
    _seed_window(session, 2007, 30)
    rc = main(["fl-fit", "--train-from", "2007-01-01", "--train-to", "2007-12-31",
               "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fl-fit" in out and "gamma=" in out and "n_informative=" in out


def test_fl_evaluate_cli(session, tmp_path, capsys, database_url):
    _seed_window(session, 2007, 30)
    _seed_window(session, 2008, 15)
    rc = main(["fl-evaluate", "--train-from", "2007-01-01", "--train-to", "2007-12-31",
               "--eval-from", "2008-01-01", "--eval-to", "2008-12-31",
               "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fl-evaluate" in out and "採否=勝率校正" in out
    assert "NLL" in out and "improved" in out


def test_fl_evaluate_overlap_is_rejected(session, database_url):
    _seed_window(session, 2007, 5)
    with pytest.raises(SystemExit):  # eval window not strictly after train -> leak guard
        main(["fl-evaluate", "--train-from", "2007-01-01", "--train-to", "2007-12-31",
              "--eval-from", "2007-06-01", "--eval-to", "2008-12-31",
              "--database-url", database_url])
