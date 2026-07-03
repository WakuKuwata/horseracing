"""Feature 055: new raw-column mapping (first_3f/prize/owner/breeder/lines) + missing→NULL."""

from __future__ import annotations

from decimal import Decimal

from horseracing_ingest.mapping import to_core_records
from horseracing_ingest.parser import ParsedRow
from tests._sjis import make_row


def _rec(**kw):
    return to_core_records(ParsedRow(1, make_row(**kw)))


def test_new_columns_mapped():
    rec = _rec()
    assert rec.race["prize_money"] == 550
    assert rec.horse["owner_name"] == "馬主一郎"
    assert rec.horse["breeder_name"] == "生産牧場"
    assert rec.horse["sire_line"] == "サンデーサイレンス系"
    assert rec.horse["damsire_line"] == "ノーザンダンサー系"
    assert rec.race_result is not None
    assert rec.race_result["first_3f"] == Decimal("35.6")


def test_missing_values_become_none_not_zero():
    rec = _rec(prize_money="", first_3f="", owner_name="", breeder_name="",
               sire_line="", damsire_line="")
    assert rec.race["prize_money"] is None
    assert rec.horse["owner_name"] is None
    assert rec.horse["breeder_name"] is None
    assert rec.horse["sire_line"] is None
    assert rec.horse["damsire_line"] is None
    assert rec.race_result["first_3f"] is None


def test_zero_prize_becomes_none():
    rec = _rec(prize_money="0")
    assert rec.race["prize_money"] is None


def test_existing_columns_unchanged():
    # widening must not disturb any previously-mapped value (byte-invariance at the parser level)
    rec = _rec()
    assert rec.race["race_id"] == "200701010101"
    assert rec.race["distance"] == 1500
    assert rec.horse["sire_name"] == "父馬"
    assert rec.race_result["last_3f"] == Decimal("36.1")
    assert rec.race_horse["odds"] == Decimal("2.5")
