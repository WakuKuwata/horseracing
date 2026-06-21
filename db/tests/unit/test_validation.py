"""US2: reusable validators (unit, no DB)."""

from __future__ import annotations

import datetime

import pytest

from horseracing_db.validation import is_in_ingest_scope, is_valid_race_id


@pytest.mark.parametrize(
    "value,expected",
    [
        ("202705021101", True),
        ("12345", False),
        ("1234567890123", False),
        ("20270502110A", False),
        ("", False),
    ],
)
def test_is_valid_race_id(value, expected):
    assert is_valid_race_id(value) is expected


def test_is_in_ingest_scope_boundary():
    assert is_in_ingest_scope(datetime.date(2007, 1, 1)) is True
    assert is_in_ingest_scope(datetime.date(2006, 12, 31)) is False
    assert is_in_ingest_scope(datetime.date(2027, 5, 1)) is True
