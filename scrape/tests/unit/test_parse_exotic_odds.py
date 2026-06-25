"""T012 (012): exotic odds parser — 6 bet types from fixture, network-free (SC-001)."""

from __future__ import annotations

from horseracing_scrape.parse.exotic_odds import parse_exotic_odds
from tests.conftest import fixture_html


def _rows_by_type(scraped):
    out: dict[str, list] = {}
    for r in scraped.rows:
        out.setdefault(r.bet_type, []).append(r)
    return out


def test_parses_all_six_bet_types_and_combos():
    scraped = parse_exotic_odds(fixture_html("exotic_odds"))
    assert scraped.key.year == 2025 and scraped.key.race_no == 11
    by = _rows_by_type(scraped)
    # unknown grid (trio-place) ignored
    assert set(by) == {"place", "quinella", "wide", "trio", "exacta", "trifecta"}
    assert {r.numbers for r in by["trio"]} == {(1, 2, 3)}
    assert {r.numbers for r in by["quinella"]} == {(1, 2), (1, 3), (2, 3)}


def test_ordered_combo_preserves_horse_number_order():
    scraped = parse_exotic_odds(fixture_html("exotic_odds"))
    by = _rows_by_type(scraped)
    exacta = {r.numbers: r.odds for r in by["exacta"]}
    assert exacta[(1, 2)] == 11.0
    assert exacta[(2, 1)] == 18.4
    assert exacta[(3, 1)] is None  # empty data-odds -> None (skipped downstream)


def test_place_is_single_number():
    scraped = parse_exotic_odds(fixture_html("exotic_odds"))
    by = _rows_by_type(scraped)
    assert {r.numbers for r in by["place"]} == {(1,), (2,), (3,)}
