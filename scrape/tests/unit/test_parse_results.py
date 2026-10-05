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
    assert first.last_3f == 34.9                       # 後3F
    assert first.corner_orders == ("7", "7", "4", "3")  # コーナー通過順


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


# --- Feature 139: FINAL win odds / popularity (列 10 / 9) -------------------------------------------
def test_parse_results_reads_final_win_odds_and_popularity_hopeful():
    r = parse_results(real_fixture(RESULTS))
    by_horse = {row.netkeiba_horse_id: (row.win_odds, row.popularity) for row in r.rows}
    assert by_horse["2022105102"] == (1.8, 1)      # 1着 クロワデュノール
    assert by_horse["2022103995"] == (19.1, 6)     # 2着
    assert by_horse["2022103478"] == (368.4, 18)   # 18着
    assert all(o is not None and p is not None for o, p in by_horse.values())
    assert sorted(p for _, p in by_horse.values()) == list(range(1, 19))


def test_parse_results_reads_final_win_odds_and_popularity_2026():
    r = parse_results(real_fixture("results_202602011206.html"))
    assert len(r.rows) == 16
    first = r.rows[0]
    assert (first.netkeiba_horse_id, first.finish_order) == ("2023106420", 1)
    assert (first.win_odds, first.popularity) == (3.4, 2)   # the page's 単勝 340円 agrees
    assert sorted(row.popularity for row in r.rows) == list(range(1, 17))


def test_dead_heat_fixture_has_no_result_table():
    # the dead-heat fixture is a payout-only page: odds come from result tables only
    from horseracing_scrape.models import NotYetPublished
    with pytest.raises(NotYetPublished):
        parse_results(real_fixture("results_deadheat.html"))


def _row_html(order: str, popularity: str, odds: str, *, header: str | None = None) -> str:
    head = ""
    if header is not None:
        head = f'<tr class="Header">{header}</tr>'
    return (
        "<html><body>race_id=202602011206"
        f'<table class="RaceTable01">{head}<tr>'
        f"<td>{order}</td><td>1</td><td>1</td>"
        '<td><a href="https://db.netkeiba.com/horse/2023106420">馬</a></td>'
        "<td>牝3</td><td>55.0</td><td>横山武</td><td>1:10.7</td><td></td>"
        f"<td>{popularity}</td><td>{odds}</td><td>36.3</td><td>1-1</td>"
        "</tr></table></body></html>"
    )


_HEADER = ("<th>着<br>順</th><th>枠</th><th>馬<br>番</th><th>馬名</th><th>性齢</th><th>斤量</th>"
           "<th>騎手</th><th>タイム</th><th>着差</th><th>人<br>気</th><th>単勝<br>オッズ</th>"
           "<th>後3F</th><th>コーナー<br>通過順</th>")


@pytest.mark.parametrize("odds", ["---", "", "**", "取消", "-"])
def test_non_numeric_win_odds_is_none(odds):
    row = parse_results(_row_html("1", "---", odds, header=_HEADER)).rows[0]
    assert row.win_odds is None and row.popularity is None
    assert row.finish_order == 1           # the result itself is still read


def test_header_and_headerless_tables_read_the_same_columns():
    with_header = parse_results(_row_html("1", "2", "3.4", header=_HEADER)).rows[0]
    headerless = parse_results(_row_html("1", "2", "3.4")).rows[0]
    assert (with_header.win_odds, with_header.popularity) == (3.4, 2)
    assert (headerless.win_odds, headerless.popularity) == (3.4, 2)


def test_moved_columns_are_not_guessed():
    # a header that no longer names 人気 / 単勝 where the reader expects them: read neither
    moved = _HEADER.replace("<th>人<br>気</th>", "<th>指数</th>")
    row = parse_results(_row_html("1", "2", "3.4", header=moved)).rows[0]
    assert row.win_odds is None and row.popularity is None
    assert row.finish_order == 1 and row.last_3f == 36.3


def test_popularity_sentinel_is_none_and_thousands_separator_is_read():
    row = parse_results(_row_html("1", "9999", "1,234.5", header=_HEADER)).rows[0]
    assert row.popularity is None
    assert row.win_odds == 1234.5
