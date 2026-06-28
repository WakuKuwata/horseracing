"""US3 (FR-003/012/013): parse_odds on a REAL netkeiba win-odds JSON fixture; fail-close on missing.

Fixture: scrape/tests/fixtures/real/odds_202406050911.json (type=1 win/place, 18 horses).
Odds JSON is keyed by 馬番 (I1) -> ScrapedOddsRow.horse_number.
"""

from __future__ import annotations

import pytest

from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.odds import parse_odds
from tests.conftest import real_fixture

RID = "202406050911"


def test_parse_odds_real_json():
    o = parse_odds(real_fixture("odds_202406050911.json"), RID)
    assert (o.key.year, o.key.track_code, o.key.race_no) == (2024, "06", 11)
    by_num = {r.horse_number: r for r in o.rows}
    assert len(by_num) == 18
    assert by_num[1].odds == 19.1 and by_num[1].popularity == 6
    assert by_num[6].odds == 1.8 and by_num[6].popularity == 1  # favourite


def test_fail_close_not_json():
    with pytest.raises(ParseError):
        parse_odds("<html>not json</html>", RID)


def test_fail_close_missing_win_key():
    with pytest.raises(ParseError):
        parse_odds('{"status":"ok","data":{"odds":{}}}', RID)
