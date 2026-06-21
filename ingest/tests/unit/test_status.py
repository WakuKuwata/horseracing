"""US2: status normalization (finished/DNF/DNS, unknown -> error)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus

from horseracing_ingest.mapping import MappingError, normalize_status
from horseracing_ingest.parser import ParsedRow
from tests._sjis import make_row

_NO_RUN = {
    "corner1": "0", "corner2": "0", "corner3": "0", "corner4": "0", "finish_time": "0.00.0",
}


def _row(**kw) -> ParsedRow:
    return ParsedRow(1, make_row(**kw))


def test_finished():
    d = normalize_status(_row(finish_order="3"))
    assert d.entry_status == EntryStatus.STARTED
    assert d.result_status == ResultStatus.FINISHED
    assert d.finish_order == 3
    assert d.make_result_row is True


def test_dnf_ran_but_no_finish():
    d = normalize_status(_row(finish_order="0", corner2="5", corner3="6", finish_time="0.00.0"))
    assert d.entry_status == EntryStatus.STARTED
    assert d.result_status == ResultStatus.STOPPED
    assert d.finish_order is None  # no pseudo last-place
    assert d.make_result_row is True


def test_dns_no_run_data():
    d = normalize_status(_row(finish_order="0", **_NO_RUN))
    assert d.entry_status == EntryStatus.CANCELLED
    assert d.make_result_row is False  # INV-1: no race_results row
    assert d.result_status is None


def test_unknown_finish_order_errors():
    with pytest.raises(MappingError):
        normalize_status(_row(finish_order="ZZ"))
