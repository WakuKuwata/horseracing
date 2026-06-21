"""US2 (SC-002): status normalization end-to-end, 3-way hard gate + dead heat."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.labels import derive_labels
from horseracing_db.models import RaceHorse, RaceResult

from horseracing_ingest.pipeline import ingest_year
from tests._sjis import make_row, write_csv

pytestmark = pytest.mark.integration

RACE_ID = "200701010101"
_NO_RUN = {
    "corner1": "0", "corner2": "0", "corner3": "0", "corner4": "0", "finish_time": "0.00.0",
}


def test_status_three_way_and_dead_heat(session, tmp_path):
    rows = [
        make_row(horse_id="H1", horse_number="1", finish_order="1"),
        make_row(horse_id="H2", horse_number="2", finish_order="1"),  # dead heat for 1st
        make_row(horse_id="H3", horse_number="3", finish_order="3"),
        # DNF: finish_order 0 with running data
        make_row(horse_id="H4", horse_number="4", finish_order="0", corner2="5", corner3="6",
                 finish_time="0.00.0"),
        # DNS: finish_order 0 with no running data
        make_row(horse_id="H5", horse_number="5", finish_order="0", **_NO_RUN),
    ]
    ingest_year(session, write_csv(tmp_path / "2007", rows))

    # entry status
    assert session.get(RaceHorse, (RACE_ID, "H4")).entry_status == EntryStatus.STARTED
    assert session.get(RaceHorse, (RACE_ID, "H5")).entry_status == EntryStatus.CANCELLED

    # INV-1: DNS has no race_results row; DNF has a non-finished row
    assert session.get(RaceResult, (RACE_ID, "H5")) is None
    assert session.get(RaceResult, (RACE_ID, "H4")).result_status == ResultStatus.STOPPED
    assert session.get(RaceResult, (RACE_ID, "H4")).finish_order is None

    # labels: only finishers, dead heat -> 2 winners
    labels = derive_labels(session, RACE_ID)
    assert {row["horse_id"] for row in labels} == {"H1", "H2", "H3"}
    assert sum(row["win"] for row in labels) == 2
    assert sum(row["top3"] for row in labels) == 3
