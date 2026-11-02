"""176 T005: 競馬場名 → 2 桁コードは netkeiba 側のコード表と一致する。"""

from __future__ import annotations

from horseracing_scrape.jra.venues import CODE_TO_VENUE_NAME, VENUE_NAME_TO_CODE, venue_code
from horseracing_scrape.venues import NETKEIBA_TO_JRAVAN_VENUE, build_race_id


def test_codes_match_netkeiba_table():
    assert set(VENUE_NAME_TO_CODE.values()) == set(NETKEIBA_TO_JRAVAN_VENUE.values())
    assert len(CODE_TO_VENUE_NAME) == 10


def test_lookup():
    assert venue_code("東京") == "05"
    assert venue_code(" 京都 ") == "08"
    assert venue_code("大井") is None
    assert venue_code(None) is None


def test_feeds_build_race_id():
    assert build_race_id(
        year=2026, track_code=venue_code("東京"), kai=4, nichime=11, race_no=11
    ) == ("202605041111")
