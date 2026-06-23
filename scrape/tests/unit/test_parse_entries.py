"""US1 (FR-013): parse_entries extracts race + horses, fail-close on missing required elements."""

from __future__ import annotations

import pytest

from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.entries import parse_entries
from tests.conftest import fixture_html


def test_parse_entries_fields():
    e = parse_entries(fixture_html("entries"))
    assert (e.race.key.year, e.race.key.track_code, e.race.key.kai, e.race.key.nichime,
            e.race.key.race_no) == (2025, "05", 2, 3, 11)
    assert e.race.distance == 1600 and e.race.track_type == "芝"
    assert len(e.horses) == 3
    h1 = e.horses[0]
    assert h1.netkeiba_horse_id == "H001" and h1.horse_number == 1 and h1.entry_status == "started"
    assert h1.netkeiba_jockey_id == "J01" and h1.weight == 55
    assert e.horses[2].entry_status == "cancelled"  # 取消が反映される


def test_fail_close_missing_race():
    with pytest.raises(ParseError):
        parse_entries("<html><body>no race</body></html>")


def test_fail_close_missing_horse_id():
    html = (
        '<div class="race" data-year="2025" data-track="05" data-kai="2" data-day="3" '
        'data-raceno="11"><table class="entries"><tr class="horse" data-number="1"></tr>'
        "</table></div>"
    )
    with pytest.raises(ParseError):
        parse_entries(html)
