"""US1: race_id derivation, venue mapping, unused-column skip (FR-002/003/004)."""

from __future__ import annotations

import pytest
from horseracing_db.validation import is_valid_race_id

from horseracing_ingest.mapping import MappingError, derive_race_id, venue_to_code
from horseracing_ingest.parser import ParsedRow
from tests._sjis import make_row


def _row(**kw) -> ParsedRow:
    return ParsedRow(1, make_row(**kw))


def test_derive_race_id_12_digits():
    # 2007 + 札幌(01) + kai(01) + nichime(01) + race(01)
    rid = derive_race_id(_row())
    assert rid == "200701010101"
    assert is_valid_race_id(rid)


def test_derive_race_id_uses_columns():
    row = _row(venue="東京", kai="3", nichime="5", race_no="11", race_date="2010.6.6")
    rid = derive_race_id(row)
    assert rid == "201005030511"
    assert is_valid_race_id(rid)


@pytest.mark.parametrize("name,code", [("札幌", "01"), ("東京", "05"), ("小倉", "10")])
def test_venue_to_code(name, code):
    assert venue_to_code(name) == code


def test_unknown_venue_errors():
    with pytest.raises(MappingError):
        venue_to_code("大井")  # 地方競馬, not a JRA course


def test_nichime_letter_extension():
    # JRA-VAN encodes meeting day >= 10 as A..F; day 10 = "A" (福島, kai 3, race 1, 2007)
    row = _row(venue="福島", kai="3", nichime="A", race_no="1", race_date="2007.11.18")
    rid = derive_race_id(row)
    assert rid == "200703031001"
    assert is_valid_race_id(rid)


def test_unused_columns_ignored():
    fields = make_row()
    fields[24] = "GARBAGE"  # col25 is skipped per research R1
    assert derive_race_id(ParsedRow(1, fields)) == "200701010101"
