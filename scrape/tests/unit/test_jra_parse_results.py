"""176 T068: レース結果(開催日ページ)の解析 — rows, laps, corners, embedded payouts."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import ResultStatus

from horseracing_scrape.jra.parse.results import parse_results_day
from horseracing_scrape.models import ParseError
from tests.jra_fixtures import JST, jra_text

D = datetime.date(2026, 10, 31)
FIX = "results_tokyo_20261031.sjis.html"


def _page():
    return parse_results_day(jra_text(FIX), expected_date=D)


def test_twelve_published_results():
    page = _page()
    assert (page.venue_code, page.kai, page.nichime) == ("05", 4, 10)
    assert page.state_counts()["races_published"] == 12
    assert [b.race_id for b in page.races] == [f"2026050410{n:02d}" for n in range(1, 13)]


def test_first_race_rows_laps_and_payouts():
    r = _page().races[0].payload
    assert r.race.post_time == datetime.datetime(2026, 10, 31, 9, 50, tzinfo=JST)
    assert (r.race.distance, r.race.track_type, r.race.weather, r.race.going) == (
        1600,
        "ダート",
        "晴",
        "良",
    )
    w = r.rows[0]
    assert (w.finish_order, w.result_status, w.horse_number) == (1, ResultStatus.FINISHED, 12)
    assert (w.horse_id, w.horse_name) == ("2024106410", "ホウオウシュウ")
    assert w.finish_time == "1:38.5" and w.margin is None
    assert w.corner_orders == ("8", "7") and w.last_3f == 37.4
    assert (w.weight, w.weight_diff) == (520, 0) and w.popularity == 2
    assert (w.jockey_id, w.trainer_id) == ("05678", "01162")
    second = r.rows[1]
    assert second.finish_order == 2 and second.margin and "1/2" in second.margin
    assert (second.weight, second.weight_diff) == (474, 2)
    assert r.lap_times == (12.3, 11.1, 12.0, 12.3, 12.7, 12.5, 12.6, 13.0)
    assert (r.pace_last_4f, r.pace_last_3f) == (50.8, 38.1)
    assert "3コーナー" in r.corner_passing and "4コーナー" in r.corner_passing
    pay = r.payouts
    assert pay.block("win").state == "complete"
    assert [(ln.numbers, ln.payout_yen) for ln in pay.block("win").lines] == [((12,), 460)]
    assert [(ln.numbers, ln.payout_yen) for ln in pay.block("trio").lines] == [((6, 11, 12), 2980)]
    assert [(ln.numbers, ln.payout_yen) for ln in pay.block("trifecta").lines] == [
        ((12, 11, 6), 15640)
    ]
    assert pay.block("bracket_quinella").lines[0].numbers == (6, 6)


def test_all_rows_carry_ids_and_popularity_is_only_a_field():
    statuses = set()
    for b in _page().races:
        for row in b.payload.rows:
            assert row.horse_id and row.jockey_id and row.trainer_id
            statuses.add(row.result_status)
            if row.result_status == ResultStatus.FINISHED:
                assert isinstance(row.popularity, int)
            else:  # 取消 rows appear on the result page too, without a time or popularity
                assert row.finish_order is None and row.finish_time is None
    assert ResultStatus.FINISHED in statuses


def test_other_day_heading_fails_closed():
    with pytest.raises(ParseError):
        parse_results_day(jra_text("derived/results_other_day.sjis.html"), expected_date=D)


def test_one_broken_block_does_not_take_the_others_down():
    html = jra_text(FIX)
    # corrupt the 3rd race's result table header: drop its 着順 column
    marker = 'id="race_result_3R"'
    i = html.find(marker)
    j = html.find('<th scope="col" class="place">', i)
    k = html.find("</th>", j) + len("</th>")
    broken = html[:j] + html[k:]
    page = parse_results_day(broken, expected_date=D)
    counts = page.state_counts()
    assert (
        counts["races_published"] == 11
        and counts["races_not_yet"] == 1
        or counts["races_parse_failed"] == 1
    )
