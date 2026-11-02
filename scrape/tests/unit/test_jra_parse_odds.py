"""176 T053: 単複オッズ(1 レース)の解析 — official_at from 「HH時MM分現在オッズ」."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus

from horseracing_scrape.jra.parse.odds import parse_win_odds
from horseracing_scrape.models import ParseError
from tests.jra_fixtures import JST, jra_text

D = datetime.date(2026, 11, 1)
FIX = "odds_tokyo11r_20261101_0822.sjis.html"


def test_tokyo_11r_0822():
    o = parse_win_odds(jra_text(FIX), expected_date=D)
    assert o.race_id == "202605041111" and o.kind == "current"
    assert o.official_at == datetime.datetime(2026, 11, 1, 8, 22, tzinfo=JST)
    first = o.rows[0]
    assert (first.horse_number, first.horse_id, first.horse_name) == (
        1,
        "2022105102",
        "クロワデュノール",
    )
    assert first.win_odds == 2.7 and (first.place_low, first.place_high) == (1.2, 1.6)
    assert first.jockey_weight == 58.0 and first.weight is None
    assert first.entry_status == EntryStatus.STARTED
    second = o.rows[1]
    assert second.horse_name == "エルトンバローズ" and second.win_odds == 216.0
    assert len({r.horse_number for r in o.rows}) == len(o.rows)
    assert all(r.win_odds is not None for r in o.rows if r.entry_status == EntryStatus.STARTED)


def test_missing_time_stamp_fails_closed():
    with pytest.raises(ParseError):
        parse_win_odds(jra_text("derived/odds_missing_time.sjis.html"), expected_date=D)


def test_prev_day_final_is_flagged_not_timed():
    html = jra_text(FIX).replace("8時22分現在オッズ", "前日最終オッズ")
    o = parse_win_odds(html, expected_date=D)
    assert o.kind == "prev_day_final" and o.official_at is None


def test_wrong_date_is_rejected():
    with pytest.raises(ParseError):
        parse_win_odds(jra_text(FIX), expected_date=datetime.date(2026, 10, 31))
