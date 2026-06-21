"""US2 / INV-1, INV-3, INV-5: status-aware label derivation."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.labels import derive_labels
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

pytestmark = pytest.mark.integration

RACE_ID = "202705021101"


def _add_horse(session, horse_id, *, entry_status=EntryStatus.STARTED):
    session.add(Horse(horse_id=horse_id))
    session.add(RaceHorse(race_id=RACE_ID, horse_id=horse_id, entry_status=entry_status))


def _setup_standard_race(session):
    session.add(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2027, 5, 1)))
    # 5 finishers
    for i, hid in enumerate(["H1", "H2", "H3", "H4", "H5"], start=1):
        _add_horse(session, hid)
        session.add(RaceResult(race_id=RACE_ID, horse_id=hid, finish_order=i,
                               result_status=ResultStatus.FINISHED))
    # non-finishers (started, but no completion)
    _add_horse(session, "H6")
    session.add(RaceResult(race_id=RACE_ID, horse_id="H6", result_status=ResultStatus.STOPPED))
    _add_horse(session, "H7")
    session.add(RaceResult(race_id=RACE_ID, horse_id="H7", result_status=ResultStatus.DISQUALIFIED))
    # non-starters (no race_results row at all)
    _add_horse(session, "H8", entry_status=EntryStatus.CANCELLED)
    _add_horse(session, "H9", entry_status=EntryStatus.EXCLUDED)
    session.flush()


def test_labels_status_aware_and_sums(session):
    _setup_standard_race(session)
    labels = derive_labels(session, RACE_ID)

    returned = {row["horse_id"] for row in labels}
    assert returned == {"H1", "H2", "H3", "H4", "H5"}, "only finished rows are labelled"

    assert sum(r["win"] for r in labels) == 1
    assert sum(r["top2"] for r in labels) == 2
    assert sum(r["top3"] for r in labels) == 3


def test_inv1_non_starters_have_no_results_row(session):
    _setup_standard_race(session)
    for hid in ("H8", "H9"):
        result = session.execute(
            select(RaceResult).where(RaceResult.race_id == RACE_ID, RaceResult.horse_id == hid)
        ).scalar_one_or_none()
        assert result is None, "cancelled/excluded horses must have no race_results row"


def test_dead_heat_allows_multiple_winners(session):
    session.add(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2027, 5, 1)))
    for hid, order in [("D1", 1), ("D2", 1), ("D3", 3)]:  # dead heat for 1st
        _add_horse(session, hid)
        session.add(RaceResult(race_id=RACE_ID, horse_id=hid, finish_order=order,
                               result_status=ResultStatus.FINISHED))
    session.flush()

    labels = derive_labels(session, RACE_ID)
    assert sum(r["win"] for r in labels) == 2, "dead heat: two horses share finish_order 1"
    assert sum(r["top2"] for r in labels) == 2
