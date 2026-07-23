"""Feature 080 US1: exotic payouts from a real netkeiba result fixture."""

from __future__ import annotations

import pytest

from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.exotic_odds import parse_exotic_odds
from tests.conftest import real_fixture

RESULTS = "results_202602011206.html"
DEAD_HEAT_RESULTS = "results_deadheat.html"


def _rows_by_type(scraped):
    out: dict[str, list] = {}
    for row in scraped.rows:
        out.setdefault(row.bet_type, []).append(row)
    return out


def test_parses_real_exotic_payouts():
    scraped = parse_exotic_odds(real_fixture(RESULTS))

    assert (
        scraped.key.year,
        scraped.key.track_code,
        scraped.key.kai,
        scraped.key.nichime,
        scraped.key.race_no,
    ) == (2026, "02", 1, 12, 6)

    actual = {
        bet_type: [(row.numbers, row.odds) for row in rows]
        for bet_type, rows in _rows_by_type(scraped).items()
    }
    assert actual == {
        "place": [((1,), 1.5), ((9,), 2.4), ((10,), 1.3)],
        "quinella": [((1, 9), 20.0)],
        "wide": [((1, 9), 6.8), ((1, 10), 2.7), ((9, 10), 5.8)],
        "exacta": [((1, 9), 32.8)],
        "trio": [((1, 9, 10), 18.9)],
        "trifecta": [((1, 9, 10), 109.4)],
    }


def test_skips_win_and_bracket_quinella():
    bet_types = {row.bet_type for row in parse_exotic_odds(real_fixture(RESULTS)).rows}

    assert "win" not in bet_types
    assert "bracket_quinella" not in bet_types
    assert bet_types == {"place", "quinella", "wide", "exacta", "trio", "trifecta"}


def test_handles_multiple_place_and_wide_payouts():
    by_type = _rows_by_type(parse_exotic_odds(real_fixture(RESULTS)))

    assert len(by_type["place"]) == 3
    assert len(by_type["wide"]) == 3


def test_parses_dead_heat_multiple_payouts():
    by_type = _rows_by_type(parse_exotic_odds(real_fixture(DEAD_HEAT_RESULTS)))
    actual = {
        bet_type: [(row.numbers, row.odds) for row in rows]
        for bet_type, rows in by_type.items()
    }

    assert actual == {
        "place": [((3,), 1.2), ((7,), 1.4), ((5,), 1.8)],
        "quinella": [((3, 7), 2.5)],
        "wide": [((3, 7), 1.6), ((3, 5), 2.1), ((7, 5), 2.3)],
        "exacta": [((3, 5), 2.0), ((7, 5), 3.5)],
        "trio": [((3, 5, 7), 4.5)],
        "trifecta": [((3, 7, 5), 6.0), ((7, 3, 5), 8.0)],
    }
    assert len(by_type["exacta"]) >= 2
    assert len(by_type["trifecta"]) >= 2
    assert len(by_type["place"]) == 3
    assert "win" not in by_type
    assert "bracket_quinella" not in by_type


def test_skips_unknown_and_garbled_rows_but_keeps_known_rows():
    html = """
    <html><body><a href="?race_id=202602011206">race</a>
      <table class="Other Payout_Detail_Table">
        <tr>
          <th>未知券種</th>
          <td class="Result"><ul><li><span>2</span></li></ul></td>
          <td class="Payout"><span>100円</span></td>
        </tr>
        <tr>
          <th>馬連</th>
          <td class="Result"><ul><li><span>2</span></li><li><span>7</span></li></ul></td>
          <td class="Payout"><span>1,230円</span></td>
        </tr>
        <tr>
          <th>馬単</th>
          <td class="Result"><ul><li><span>2</span></li><li><span>bad</span></li></ul></td>
          <td class="Payout"><span>2,000円</span></td>
        </tr>
      </table>
    </body></html>
    """

    scraped = parse_exotic_odds(html)

    assert [(row.bet_type, row.numbers, row.odds) for row in scraped.rows] == [
        ("quinella", (2, 7), 12.3)
    ]


def test_no_payout_table_raises():
    with pytest.raises(ParseError, match="no payout table"):
        parse_exotic_odds("<html><body>race_id=202602011206</body></html>")


def test_selection_payout_count_mismatch_raises():
    html = """
    <html><body><a href="?race_id=202602011206">race</a>
      <table class="Payout_Detail_Table">
        <tr>
          <th>複勝</th>
          <td class="Result"><div><span>1</span></div><div><span>9</span></div></td>
          <td class="Payout"><span>150円</span></td>
        </tr>
      </table>
    </body></html>
    """

    with pytest.raises(ParseError, match="selection/payout count mismatch"):
        parse_exotic_odds(html)
