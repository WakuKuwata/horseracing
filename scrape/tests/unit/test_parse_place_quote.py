"""Phase 0-2: 複勝 (place) quote + observation time parsed from the SAME win-odds payload.

The win-odds JSON we already fetch daily carries data.odds["2"] (複勝 [下限, 上限, 人気] per horse)
and data.official_datetime. Both were previously parsed away. Reading them costs 0 extra requests.

Discipline under test:
- both range ends or neither (a one-sided range is not a market quote)
- 複勝人気 is NOT 単勝人気
- group "2" is OPTIONAL: its absence must never take the win odds down with it
- official_datetime is JST wall clock -> tz-aware
"""

from __future__ import annotations

import datetime
import json

from horseracing_scrape.parse.odds import parse_odds
from tests.conftest import real_fixture

RID = "202406050911"


def _payload(win: dict, place: dict | None = None, official: str | None = None) -> str:
    odds: dict = {"1": win}
    if place is not None:
        odds["2"] = place
    data: dict = {"odds": odds}
    if official is not None:
        data["official_datetime"] = official
    return json.dumps({"status": "result", "data": data})


def test_place_quote_from_real_fixture():
    o = parse_odds(real_fixture("odds_202406050911.json"), RID)
    by_num = {r.horse_number: r for r in o.place_rows}
    assert len(by_num) == 18
    # 馬番1: 複勝 2.4-3.9, 複勝人気 5 — while 単勝 is 19.1 / 単勝人気 6.
    assert (by_num[1].odds_low, by_num[1].odds_high) == (2.4, 3.9)
    assert by_num[1].popularity == 5
    win_by_num = {r.horse_number: r for r in o.rows}
    assert win_by_num[1].popularity == 6, "複勝人気 must not overwrite 単勝人気"
    assert all(r.odds_low <= r.odds_high for r in o.place_rows)


def test_official_datetime_is_parsed_as_jst():
    o = parse_odds(real_fixture("odds_202406050911.json"), RID)
    assert o.official_at == datetime.datetime(
        2024, 12, 28, 15, 50, 17, tzinfo=datetime.timezone(datetime.timedelta(hours=9))
    )
    # tz-aware, so it compares correctly against UTC-stored timestamps.
    assert o.official_at.utcoffset() == datetime.timedelta(hours=9)


def test_missing_place_group_keeps_win_odds():
    o = parse_odds(_payload({"01": ["19.1", "0.0", "6"]}), RID)
    assert o.place_rows == ()
    assert o.rows[0].odds == 19.1  # win survives a payload without 複勝


def test_missing_official_datetime_is_none():
    o = parse_odds(_payload({"01": ["19.1", "0.0", "6"]}), RID)
    assert o.official_at is None


def test_unparsable_official_datetime_is_none():
    o = parse_odds(_payload({"01": ["19.1", "0.0", "6"]}, official="not-a-time"), RID)
    assert o.official_at is None


def test_partial_or_invalid_range_is_dropped_to_none():
    place = {
        "01": ["2.4", "---.-", "5"],   # upper missing
        "02": ["---.-", "3.9", "6"],   # lower missing
        "03": ["0.0", "0.0", "7"],     # not yet priced
        "04": ["5.0", "2.0", "8"],     # inverted
        "05": ["1.5", "2.1", "1"],     # valid
    }
    o = parse_odds(_payload({"01": ["19.1", "0.0", "6"]}, place=place), RID)
    by_num = {r.horse_number: r for r in o.place_rows}
    for n in (1, 2, 3, 4):
        assert (by_num[n].odds_low, by_num[n].odds_high) == (None, None), n
    assert (by_num[5].odds_low, by_num[5].odds_high) == (1.5, 2.1)


def test_status_is_not_persisted_on_the_quote():
    """`status` is "result" even ~1.5y after the race, so it is a response flag, not a settled
    discriminator. It must not leak onto the parsed quote as if it meant something."""
    o = parse_odds(real_fixture("odds_202406050911.json"), RID)
    assert not hasattr(o, "status")
