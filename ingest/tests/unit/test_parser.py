"""US1: SJIS streaming parse, 73-column validation, per-line error reporting."""

from __future__ import annotations

from horseracing_ingest import layout
from horseracing_ingest.parser import ParsedRow, RowError, parse_rows
from tests._sjis import make_row, write_csv


def test_parse_valid_rows(tmp_path):
    p = write_csv(tmp_path / "2007", [make_row(), make_row(horse_id="X2", horse_number="2")])
    items = list(parse_rows(p))
    assert len(items) == 2
    assert all(isinstance(i, ParsedRow) for i in items)
    assert items[0].fields[layout.HORSE_NAME] == "テスト馬"
    assert len(items[0].fields) == layout.EXPECTED_COLUMNS


def test_bad_column_count_is_row_error(tmp_path):
    p = tmp_path / "bad"
    with open(p, "w", encoding="cp932", newline="") as f:
        f.write("a,b,c\n")
    items = list(parse_rows(p))
    assert len(items) == 1
    assert isinstance(items[0], RowError)
    assert "73 columns" in items[0].reason


def test_cp932_decode_error_is_row_error(tmp_path):
    p = tmp_path / "bad"
    with open(p, "wb") as f:
        f.write(b"\x81\xffnot-decodable\n")  # 0x81 lead + invalid trail
    items = list(parse_rows(p))
    assert len(items) == 1
    assert isinstance(items[0], RowError)
    assert "decode" in items[0].reason
