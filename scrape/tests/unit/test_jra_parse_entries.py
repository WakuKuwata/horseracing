"""176 T051: 出馬表(開催日ページ)の解析 — real fixture + fail-closed derivatives (SC-007)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus

from horseracing_scrape.jra.models import JraEntry
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.models import ParseError
from tests.jra_fixtures import JST, jra_text

D = datetime.date(2026, 11, 1)
FIX = "entries_tokyo_20261101.sjis.html"


def _page():
    return parse_entries_day(jra_text(FIX), expected_date=D)


def test_day_page_has_twelve_published_races():
    page = _page()
    assert (page.venue_code, page.kai, page.nichime) == ("05", 4, 11)
    assert page.state_counts() == {
        "races_published": 12,
        "races_not_yet": 0,
        "races_cancelled": 0,
        "races_parse_failed": 0,
    }
    assert [b.race_id for b in page.races] == [f"2026050411{n:02d}" for n in range(1, 13)]


def test_first_race_header_and_first_horse():
    e: JraEntry = _page().races[0].payload
    r = e.race
    assert r.race_name == "2歳未勝利" and r.race_class == "未勝利"
    assert r.post_time == datetime.datetime(2026, 11, 1, 10, 0, tzinfo=JST)
    assert (r.distance, r.track_type, r.prize_money) == (1400, "芝", 590)
    h = e.horses[0]
    assert (h.frame, h.horse_number, h.horse_name) == (1, 1, "インサイドエッジ")
    assert h.horse_id == "2024107186"
    assert (h.jockey_id, h.jockey_name) == ("05339", "C.ルメール")
    assert (h.trainer_id, h.trainer_name) == ("01165", "和田 勇介")
    assert (h.sex, h.age, h.jockey_weight) == ("牝", 2, 55.0)
    assert h.weight is None and h.weight_diff is None  # morning page: not yet weighed
    assert h.entry_status == EntryStatus.STARTED and h.unknown_id_kinds == ()


def test_cancelled_horse_keeps_identity_but_loses_its_number():
    r4 = next(b for b in _page().races if b.race_no == 4).payload
    cancelled = [h for h in r4.horses if h.entry_status == EntryStatus.CANCELLED]
    assert len(cancelled) == 1
    c = cancelled[0]
    assert c.horse_name == "サルサベガ" and c.horse_id == "2024102799"
    assert c.horse_number is None and c.frame == 4
    # changes page says 5番; the parser must NOT infer it from row order
    numbers = [h.horse_number for h in r4.horses if h.horse_number is not None]
    assert 5 not in numbers


def test_every_started_horse_has_all_three_ids():
    for b in _page().races:
        for h in b.payload.horses:
            assert h.horse_id and h.jockey_id and h.trainer_id, (b.race_id, h.horse_name)


def test_graded_race_is_tagged():
    r11 = next(b for b in _page().races if b.race_no == 11).payload
    assert r11.race.grade in {"G1", "G2", "G3"}


def test_missing_column_fails_closed_for_every_block():
    # Every block lacks the column → no block parses → the PAGE fails (no partial values).
    with pytest.raises(ParseError):
        parse_entries_day(jra_text("derived/entries_missing_h_weight.sjis.html"), expected_date=D)
    # A single broken block, by contrast, is isolated: drop the column from the 3rd race only.
    html = jra_text(FIX)
    i = html.find('id="syutsuba_3R"')
    j = html.find('<th class="h_weight" scope="col">', i)
    k = html.find("</th>", j) + len("</th>")
    page = parse_entries_day(html[:j] + html[k:], expected_date=D)
    counts = page.state_counts()
    assert counts["races_parse_failed"] == 1 and counts["races_published"] == 11
    failed = next(b for b in page.races if b.state == "parse_failed")
    assert failed.payload is None and "h_weight" in (failed.error or "")


def test_other_day_heading_is_rejected():
    with pytest.raises(ParseError):
        parse_entries_day(jra_text("derived/entries_other_day.sjis.html"), expected_date=D)
    # and the real page is rejected for a wrong expected_date too
    with pytest.raises(ParseError):
        parse_entries_day(jra_text(FIX), expected_date=datetime.date(2026, 11, 2))


def test_not_an_entries_page():
    with pytest.raises(ParseError):
        parse_entries_day("<html><body>nothing</body></html>", expected_date=D)
