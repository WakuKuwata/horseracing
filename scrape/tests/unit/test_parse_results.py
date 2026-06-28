"""US2 (FR-002/012): parse_results on a REAL netkeiba result fixture; fail-close on missing/unknown.

Fixture: scrape/tests/fixtures/real/results_202406050911.html (Hopeful S, 18 finishers).
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import ResultStatus

from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.results import parse_results
from horseracing_scrape.upsert import parse_netkeiba_time
from tests.conftest import real_fixture

RESULTS = "results_202406050911.html"


def test_parse_results_real():
    r = parse_results(real_fixture(RESULTS))
    k = r.race.key if hasattr(r, "race") else r.key
    assert (k.year, k.track_code, k.race_no) == (2024, "06", 11)
    assert len(r.rows) == 18
    first = r.rows[0]
    assert first.finish_order == 1 and first.result_status == ResultStatus.FINISHED
    assert first.netkeiba_horse_id == "2022105102" and first.finish_time == "2:00.5"


def test_finish_time_to_timedelta():
    import datetime
    assert parse_netkeiba_time("2:00.5") == datetime.timedelta(minutes=2, milliseconds=500)
    assert parse_netkeiba_time("59.8") == datetime.timedelta(seconds=59, milliseconds=800)
    assert parse_netkeiba_time("") is None and parse_netkeiba_time(None) is None


def test_fail_close_missing_table():
    with pytest.raises(ParseError):
        parse_results("<html><body>race_id=202406050911 no table</body></html>")


def test_fail_close_unknown_status():
    html = (
        "<html><body>race_id=202406050911"
        '<table class="RaceTable01"><tr>'
        "<td>ワープ</td><td>3</td><td>6</td>"
        '<td><a href="https://db.netkeiba.com/horse/2022105102">馬</a></td>'
        "<td>牡2</td><td>56.0</td><td>北村友</td><td></td>"
        "</tr></table></body></html>"
    )
    with pytest.raises(ParseError):
        parse_results(html)
