"""③ day discovery: parse_race_list / discover_races extract deduped, ordered race_ids."""

from __future__ import annotations

import pytest

from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.race_list import parse_race_list
from horseracing_scrape.pipeline import discover_races
from horseracing_scrape.urls import race_list_url
from tests.conftest import real_fixture

# A race-list fragment: two races at Tokyo (05), each linking shutuba + result (duplicate ids),
# plus one non-JRA venue id (65 = 大井/NAR) that is still a valid 12-digit id.
_FRAGMENT = """
<div class="RaceList_Box">
  <li><a href="../race/shutuba.html?race_id=202505020310">10R</a>
      <a href="../race/result.html?race_id=202505020310">result</a></li>
  <li><a href="../race/shutuba.html?race_id=202505020311">11R</a>
      <a href="../race/result.html?race_id=202505020311">result</a></li>
  <li><a href="../race/shutuba.html?race_id=202565010101">NAR</a></li>
</div>
"""


def test_parse_race_list_dedupes_and_orders():
    out = parse_race_list(_FRAGMENT, "20250503")
    assert out.kaisai_date == "20250503"
    # first-seen order, deduped (each race appears via shutuba + result link)
    assert out.race_ids == ("202505020310", "202505020311", "202565010101")


def test_parse_race_list_empty_payload_fails_close():
    with pytest.raises(ParseError):
        parse_race_list("", "20250503")


def test_parse_race_list_no_races_is_empty_not_error():
    out = parse_race_list("<html><body>no racing today</body></html>", "20250101")
    assert out.race_ids == ()


def test_discover_races_filters_to_valid_12digit():
    # discover_races fetches race_list_url and keeps only valid 12-digit ids
    url = race_list_url("20250503")
    fetcher = FixtureFetcher({url: _FRAGMENT})
    listing = discover_races(fetcher, "20250503")
    assert listing.race_ids == ("202505020310", "202505020311", "202565010101")
    assert all(len(r) == 12 and r.isdigit() for r in listing.race_ids)


def test_real_race_list_fixture():
    # REAL netkeiba race_list_sub fragment (2024-12-28): 中山(06)×12 + 京都(08)×12 = 24 races.
    out = parse_race_list(real_fixture("race_list_20241228.html"), "20241228")
    assert len(out.race_ids) == 24
    assert "202406050911" in out.race_ids  # Hopeful S
    assert {r[4:6] for r in out.race_ids} == {"06", "08"}
