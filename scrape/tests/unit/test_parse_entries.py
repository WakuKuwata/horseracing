"""US1 (FR-001/012/013): parse_entries on a REAL netkeiba shutuba fixture; fail-close on missing.

Fixture: scrape/tests/fixtures/real/entries_202406050911.html (Hopeful S G1, 中山 2024-12-28 11R,
18 horses). See fixtures/real/manifest.json for provenance (url/fetched_at/sha256).
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.entries import parse_entries
from tests.conftest import real_fixture

ENTRIES = "entries_202406050911.html"


def test_parse_entries_race_meta():
    e = parse_entries(real_fixture(ENTRIES))
    k = e.race.key
    assert (k.year, k.track_code, k.kai, k.nichime, k.race_no) == (2024, "06", 5, 9, 11)
    assert e.race.distance == 2000 and e.race.track_type == "芝"
    assert e.race.going == "良" and e.race.weather == "晴"
    assert e.race.race_date == datetime.date(2024, 12, 28)
    # C: race name / grade / post time
    assert e.race.race_name == "ホープフルS"
    assert e.race.grade == "G1"                         # Icon_GradeType1
    assert e.race.post_time is not None
    assert (e.race.post_time.hour, e.race.post_time.minute) == (15, 40)  # "15:40発走"


def test_parse_entries_horses():
    e = parse_entries(real_fixture(ENTRIES))
    assert len(e.horses) == 18
    h1 = e.horses[0]
    assert h1.netkeiba_horse_id == "2022103995"
    assert h1.horse_number == 1 and h1.frame == 1
    assert h1.sex == "牡" and h1.age == 2
    assert h1.netkeiba_jockey_id == "01126" and h1.netkeiba_trainer_id == "01157"
    assert h1.entry_status == "started"
    # weight = 馬体重 (body weight, from "484 (0)") — NOT 斤量. Matches JRA-VAN race_horses.weight.
    assert h1.weight == 484
    assert h1.weight_diff == 0          # from the same "484 (0)" cell
    assert h1.jockey_weight == 56.0     # 斤量 (impost), cell after 性齢
    nums = sorted(h.horse_number for h in e.horses)
    assert nums == list(range(1, 19))


def test_fail_close_missing_table():
    with pytest.raises(ParseError):
        parse_entries("<html><body><div>no shutuba</div></body></html>")


def test_fail_close_missing_race_id():
    html = '<html><body><table class="Shutuba_Table"><tr class="HorseList"></tr></table></body></html>'
    with pytest.raises(ParseError):
        parse_entries(html)


def test_grade_icon_scoped_to_race_name():
    # a non-graded race whose page carries a STRAY grade icon elsewhere (nav/sidebar) must NOT be
    # mislabeled — grade is read only from inside .RaceName.
    html = (
        "<html><head>"
        '<link rel="canonical" '
        'href="https://race.netkeiba.com/race/shutuba.html?race_id=202505040301" />'
        "</head><body>"
        '<div class="OtherRace"><span class="Icon_GradeType Icon_GradeType3">G3</span></div>'
        '<div class="RaceName">２歳未勝利</div>'
        '<div class="RaceData01">15:40発走 / 芝1600m</div>'
        '<table class="Shutuba_Table"><tr class="HorseList">'
        '<td class="Waku1">1</td><td class="Umaban1">1</td>'
        '<td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2023105362">馬</a></td>'
        '<td class="Barei">牝2</td><td class="Txt_C">55.0</td>'
        "</tr></table></body></html>"
    )
    e = parse_entries(html)
    assert e.race.race_name == "２歳未勝利"
    assert e.race.grade is None  # stray G3 icon outside .RaceName is ignored
