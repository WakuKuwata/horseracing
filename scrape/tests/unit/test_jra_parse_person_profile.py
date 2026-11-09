"""178 T027: the person-profile parser reads 生年月日・名前・所属 by label from a synthetic page
(no personal data of a real person is used) and refuses a page without a birth date."""

from __future__ import annotations

import datetime

import pytest

from horseracing_scrape.jra.identity import JOCKEY, TRAINER, official_id
from horseracing_scrape.jra.parse.person import parse_person_profile
from horseracing_scrape.models import ParseError

_DL_PAGE = """
<html><head><meta charset="Shift_JIS"><title>騎手プロフィール</title></head><body>
<div class="profile"><h1>試験　太郎 騎手</h1>
<dl>
<dt>所属</dt><dd>美浦</dd>
<dt>生年月日</dt><dd>1990年3月4日</dd>
<dt>初免許年</dt><dd>2009年</dd>
</dl></div></body></html>
"""

_TABLE_PAGE = """
<html><body><h2>試験　花子</h2>
<table><tr><th>所　属</th><td>栗東（厩舎長）</td></tr>
<tr><th>生 年 月 日</th><td>１９７５年１１月２５日</td></tr></table>
</body></html>
"""

_NO_BIRTH = "<html><body><h1>名前だけ</h1><dl><dt>所属</dt><dd>美浦</dd></dl></body></html>"


def test_definition_list_layout():
    p = parse_person_profile(_DL_PAGE, kind=JOCKEY, canonical_id="99901")
    assert p.kind == JOCKEY and p.canonical_id == "99901"
    assert p.name == "試験 太郎"
    assert p.birth_date == datetime.date(1990, 3, 4) and p.affiliation == "美浦"


def test_table_layout_with_full_width_digits_and_spaced_labels():
    p = parse_person_profile(_TABLE_PAGE, kind=TRAINER, canonical_id="99902")
    assert p.birth_date == datetime.date(1975, 11, 25) and p.affiliation == "栗東"
    assert p.name == "試験 花子"


def test_missing_birth_date_is_refused():
    with pytest.raises(ParseError):
        parse_person_profile(_NO_BIRTH, kind=JOCKEY, canonical_id="99903")
    with pytest.raises(ValueError):
        parse_person_profile(_DL_PAGE, kind="horse", canonical_id="x")


def test_canonical_id_comes_from_the_official_url_pattern():
    assert official_id(JOCKEY, "pw04kmk001019/AB") == "01019"
    assert official_id(TRAINER, "pw05cmk001069/CD") == "01069"
    assert official_id(JOCKEY, "pw05cmk001069/CD") is None


_REAL_SHAPE = """
<html><head><title>騎手情報　JRA</title></head><body>
<h1> </h1>
<h2>検索ウィンドウ</h2>
<h2>緊急情報</h2>
<div class="main" id="meikan_jockey_detail">
<h1>騎手情報引退試験 太郎（シケン タロウ）</h1>
<div class="profile"><div class="contents_header"><h2>プロフィール</h2></div>
<div class="inner"><div class="data"><ul>
<li><dl><dt>生年月日</dt><dd>1982年11月14日</dd></dl></li>
<li><dl><dt>所属</dt><dd>フリー</dd></dl></li>
</ul></div></div></div></div>
<h2>JRAからのお知らせ</h2>
</body></html>
"""


def test_real_page_shape_takes_the_name_from_the_second_h1_not_the_site_chrome():
    """R3 (2026-11-09): the first <h1> is empty and the first <h2> is 検索ウィンドウ; the name sits in
    the second <h1> behind 騎手情報 + a status word, with the kana in parentheses."""

    p = parse_person_profile(_REAL_SHAPE, kind=JOCKEY, canonical_id="01068")
    assert p.name == "試験 太郎"
    assert p.birth_date == datetime.date(1982, 11, 14) and p.affiliation == "フリー"
    trainer = _REAL_SHAPE.replace("騎手情報引退", "調教師情報").replace(
        "meikan_jockey", "meikan_trainer"
    )
    assert parse_person_profile(trainer, kind=TRAINER, canonical_id="01069").name == "試験 太郎"
