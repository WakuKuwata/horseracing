"""US2/US3 (FR-013): parse_odds / parse_results extract values, status mapping, fail-close."""

from __future__ import annotations

import pytest
from horseracing_db.enums import ResultStatus

from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.odds import parse_odds
from horseracing_scrape.parse.results import parse_results
from tests.conftest import fixture_html


def test_parse_odds():
    o = parse_odds(fixture_html("odds"))
    by_id = {r.netkeiba_horse_id: r for r in o.rows}
    assert by_id["H001"].odds == 3.4 and by_id["H001"].popularity == 2
    assert by_id["H003"].odds is None  # empty odds -> None (excluded at upsert)


def test_parse_results_status_mapping():
    r = parse_results(fixture_html("results"))
    by_id = {x.netkeiba_horse_id: x for x in r.rows}
    assert by_id["H001"].finish_order == 1 and by_id["H001"].result_status == ResultStatus.FINISHED
    assert by_id["H003"].result_status == ResultStatus.STOPPED and by_id["H003"].finish_order is None


def test_results_unknown_status_fail_close():
    html = (
        '<div class="race" data-year="2025" data-track="05" data-kai="2" data-day="3" '
        'data-raceno="11"><table class="results"><tr class="horse" data-horse-id="H001" '
        'data-status="teleported"></tr></table></div>'
    )
    with pytest.raises(ParseError):
        parse_results(html)
