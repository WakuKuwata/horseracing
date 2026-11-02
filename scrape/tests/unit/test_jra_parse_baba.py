"""176 T085: 馬場断片(含水率・クッション値)と accessJ の解析、年補正。"""

from __future__ import annotations

import datetime

import pytest

from horseracing_scrape.jra.parse.baba import (
    measured_at_from,
    parse_cushion,
    parse_kaisai_info,
    parse_moisture,
)
from horseracing_scrape.models import ParseError
from tests.jra_fixtures import JST, fetched_at, jra_text

FETCHED = fetched_at("baba_cushion_20261101.sjis.html")


def test_cushion_tokyo_latest():
    readings = parse_cushion(jra_text("baba_cushion_20261101.sjis.html"), fetched_at=FETCHED)
    tokyo = [r for r in readings if r.venue_code == "05"]
    assert tokyo[0].measured_at == datetime.datetime(2026, 10, 31, 7, 0, tzinfo=JST)
    assert (
        tokyo[0].item == "cushion_turf"
        and tokyo[0].value_num == 9.6
        and tokyo[0].time_basis == "measured"
    )
    assert {r.venue_code for r in readings} >= {"05", "08"}
    assert all(r.measured_at <= FETCHED for r in readings)


def test_moisture_four_items_per_time():
    readings = parse_moisture(jra_text("baba_moist_20261101.sjis.html"), fetched_at=FETCHED)
    t0 = datetime.datetime(2026, 10, 31, 5, 30, tzinfo=JST)
    tokyo = {r.item: r for r in readings if r.venue_code == "05" and r.measured_at == t0}
    assert set(tokyo) == {
        "moisture_turf_goal",
        "moisture_turf_corner4",
        "moisture_dirt_goal",
        "moisture_dirt_corner4",
    }
    assert (
        tokyo["moisture_turf_goal"].value_num == 12.9
        and tokyo["moisture_turf_goal"].value_text == "hard"
    )
    assert tokyo["moisture_dirt_corner4"].value_num == 5.9
    keys = [(r.venue_code, r.measured_at, r.item) for r in readings]
    assert len(keys) == len(set(keys))  # the PK of jra_track_conditions


def test_year_correction():
    jan = datetime.datetime(2027, 1, 3, 9, 0, tzinfo=JST)
    assert measured_at_from("12月27日（土曜）7時00分", fetched_at=jan) == datetime.datetime(
        2026, 12, 27, 7, 0, tzinfo=JST
    )
    assert measured_at_from("1月3日（日曜）7時00分", fetched_at=jan) == datetime.datetime(
        2027, 1, 3, 7, 0, tzinfo=JST
    )
    with pytest.raises(ParseError):
        measured_at_from("7時00分", fetched_at=jan)


def test_access_j_is_shift_jis_json_with_fetched_basis():
    readings = parse_kaisai_info(jra_text("accessJ_20261101.json"), fetched_at=FETCHED)
    by = {(r.venue_code, r.item): r for r in readings}
    assert by[("05", "going_turf")].value_text == "良"
    assert by[("08", "going_dirt")].value_text == "稍重"
    assert by[("05", "weather")].value_text == "晴"
    assert all(r.time_basis == "fetched" and r.measured_at == FETCHED for r in readings)
    with pytest.raises(ParseError):
        parse_kaisai_info("not json", fetched_at=FETCHED)
