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


# --- graded races must carry their grade in race_class (grade-lost-at-cutover) -----------------
#
# JRA-VAN wrote the grade INTO race_class as full-width `Ｇ１`; netkeiba calls the same race
# `オープン` and puts the grade in a separate field the feature layer never reads. Downstream
# race_class is BOTH a categorical model input and the source of the ordered class rank, so after
# the cutover every graded race looked like a plain open race — measured at winner NLL −0.0129
# over the 484 races it reaches.


def _graded_html(icon: str, race_class_text: str = "オープン", *, name_wrap: bool = True) -> str:
    icon_el = f'<span class="Icon_GradeType {icon}">g</span>' if icon else ""
    inside = icon_el if name_wrap else ""
    outside = "" if name_wrap else f'<div class="OtherRace">{icon_el}</div>'
    return (
        "<html><head>"
        '<link rel="canonical" '
        'href="https://race.netkeiba.com/race/shutuba.html?race_id=202505040301" />'
        "</head><body>"
        f"{outside}"
        f'<div class="RaceName">{inside}テストS</div>'
        '<div class="RaceData01">15:40発走 / 芝2000m</div>'
        f'<div class="RaceData02">1回 東京 3日目 サラ系3歳以上 {race_class_text} 18頭</div>'
        '<table class="Shutuba_Table"><tr class="HorseList">'
        '<td class="Waku1">1</td><td class="Umaban1">1</td>'
        '<td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2023105362">馬</a></td>'
        '<td class="Barei">牝3</td><td class="Txt_C">55.0</td>'
        "</tr></table></body></html>"
    )


def test_real_g1_fixture_carries_the_grade_into_race_class():
    e = parse_entries(real_fixture(ENTRIES))
    assert e.race.race_class == "Ｇ１"
    assert e.race.grade == "G1"  # the supplier's own value is kept as provenance


@pytest.mark.parametrize(
    ("icon", "expect_class", "expect_grade"),
    [("Icon_GradeType1", "Ｇ１", "G1"),
     ("Icon_GradeType2", "Ｇ２", "G2"),
     ("Icon_GradeType3", "Ｇ３", "G3")],
)
def test_each_grade_icon_sets_the_matching_class(icon, expect_class, expect_grade):
    e = parse_entries(_graded_html(icon))
    assert e.race.race_class == expect_class
    assert e.race.grade == expect_grade


def test_spelling_is_pinned_to_the_exact_codepoints_training_saw():
    """Not a style preference. race_class is a CATEGORICAL model input, so the raw string IS the
    category — a half-width `G1` would be a token fifteen years of training data never contained,
    and the model would treat it as unseen. Pin the code points so a future 'tidy-up' to ASCII
    cannot pass silently."""
    e = parse_entries(_graded_html("Icon_GradeType1"))
    assert [hex(ord(c)) for c in e.race.race_class] == ["0xff27", "0xff11"]  # FULLWIDTH G, ONE


def test_ungraded_open_race_is_left_alone():
    e = parse_entries(_graded_html("", race_class_text="オープン"))
    assert e.race.grade is None
    assert e.race.race_class == "オープン"  # not every open race is a graded one


def test_conditions_race_is_left_alone():
    e = parse_entries(_graded_html("", race_class_text="2勝"))
    assert e.race.race_class == "2勝"


def test_stray_icon_outside_race_name_does_not_promote_the_class():
    """The scoping bug this guards against would otherwise relabel a plain race as a G3 — and now
    it would corrupt race_class too, not just the grade field."""
    e = parse_entries(_graded_html("Icon_GradeType3", name_wrap=False))
    assert e.race.grade is None
    assert e.race.race_class == "オープン"


def test_reparsing_the_same_page_cannot_regress_the_class():
    """The failure mode that survives a green unit suite: the override silently stops firing on a
    later re-scrape and the row quietly reverts to オープン. Parse twice and require stability."""
    html = _graded_html("Icon_GradeType2")
    first = parse_entries(html).race.race_class
    second = parse_entries(html).race.race_class
    assert first == second == "Ｇ２"
