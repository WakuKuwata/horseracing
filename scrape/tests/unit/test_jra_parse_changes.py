"""176 T052: 変更情報(accessI)の解析."""

from __future__ import annotations

import datetime

import pytest

from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.models import ParseError
from tests.jra_fixtures import JST, jra_text

D = datetime.date(2026, 11, 1)


def test_changes_tokyo_20261101():
    changes = parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)
    by_race = {(c.race_id, c.kind): c for c in changes}
    c4 = by_race[("202605041104", "cancel")]
    assert (c4.horse_number, c4.horse_name) == (5, "サルサベガ")
    c6 = by_race[("202605041106", "cancel")]
    assert (c6.horse_number, c6.horse_name) == (13, "ソリッドベーシス")
    p10 = by_race[("202605041110", "post_time")]
    assert p10.old_post_time == datetime.datetime(2026, 11, 1, 15, 0, tzinfo=JST)
    assert p10.new_post_time == datetime.datetime(2026, 11, 1, 15, 1, tzinfo=JST)
    assert all(c.race_id.startswith("2026") for c in changes)


def test_stale_page_is_refused():
    with pytest.raises(ParseError):
        parse_changes(
            jra_text("changes_20261101.sjis.html"), expected_date=datetime.date(2026, 11, 8)
        )


def test_page_without_changes_is_an_empty_tuple():
    html = (
        "<html><body><p>11月1日（日曜）　開催情報</p><div class='kaisai_list_unit'><table><caption>4回東京11日"
        "</caption><tbody><tr><th>1</th><td></td></tr></tbody></table></div></body></html>"
    )
    assert parse_changes(html, expected_date=D) == ()


def test_jockey_change_formats():
    base = (
        "<html><body><p>11月1日（日曜）　開催情報</p><div class='kaisai_list_unit'><table>"
        "<caption>4回東京11日</caption><tbody>"
    )
    row = "<tr class='change'><th>3</th><td><dl><dt>騎手変更</dt><dd>7番 テスト馬 戸崎 圭太 → 横山 武史</dd></dl></td></tr>"
    changes = parse_changes(base + row + "</tbody></table></div></body></html>", expected_date=D)
    assert len(changes) == 1
    c = changes[0]
    assert (c.race_id, c.kind, c.horse_number) == ("202605041103", "jockey", 7)
    assert c.old_jockey_name == "戸崎 圭太" and c.new_jockey_name == "横山 武史"
    row2 = "<tr class='change'><th>3</th><td><dl><dt>騎手変更</dt><dd>7番 テスト馬 なぞ</dd></dl></td></tr>"
    with pytest.raises(ParseError):
        parse_changes(base + row2 + "</tbody></table></div></body></html>", expected_date=D)
