"""Feature 139 (T005): the official 単勝 payout from the result page (FR-002 / SC-001)."""

from __future__ import annotations

import pytest

from horseracing_scrape.models import ParseError, ScrapedWinPayout
from horseracing_scrape.parse.exotic_odds import parse_exotic_odds, parse_win_payouts
from tests.conftest import real_fixture


def _pairs(html: str) -> list[tuple[int, int]]:
    return [(p.horse_number, p.payout_yen) for p in parse_win_payouts(html)]


def test_real_result_page_single_winner():
    payouts = parse_win_payouts(real_fixture("results_202602011206.html"))
    assert payouts == [ScrapedWinPayout(horse_number=1, payout_yen=340)]


def test_real_dead_heat_gives_one_row_per_winner():
    assert _pairs(real_fixture("results_deadheat.html")) == [(3, 200), (7, 350)]


def test_page_without_payout_table_is_empty():
    # the trimmed Hopeful S fixture carries the result table only
    assert parse_win_payouts(real_fixture("results_202406050911.html")) == []


def test_exotic_parser_still_never_returns_win():
    bet_types = {r.bet_type for r in parse_exotic_odds(real_fixture("results_deadheat.html")).rows}
    assert "win" not in bet_types


def _table(result: str, payout: str, *, tr_class: str = "Tansho", label: str = "単勝") -> str:
    return (
        '<table class="Payout_Detail_Table"><tbody>'
        f'<tr class="{tr_class}"><th>{label}</th>'
        f'<td class="Result">{result}</td>'
        f'<td class="Payout"><span>{payout}</span></td>'
        '<td class="Ninki"><span>1人気</span></td></tr>'
        "</tbody></table>"
    )


def _page(result: str, payout: str, **kw) -> str:
    return f"<html><body>{_table(result, payout, **kw)}</body></html>"


def test_padding_spans_and_thousands_separator():
    html = _page("<div><span>12</span></div><div><span></span></div>", "12,340円")
    assert _pairs(html) == [(12, 12340)]


def test_ganbei_100_yen_is_legal():
    assert _pairs(_page("<div><span>5</span></div>", "100円")) == [(5, 100)]


def test_row_found_by_label_when_class_is_missing():
    html = _page("<div><span>4</span></div>", "560円", tr_class="Other")
    assert _pairs(html) == [(4, 560)]


def test_no_tansho_row_is_empty():
    html = _page("<div><span>4</span></div>", "560円", tr_class="Fukusho", label="複勝")
    assert parse_win_payouts(html) == []


def test_empty_row_is_empty():
    assert parse_win_payouts(_page("<div><span></span></div>", "")) == []


@pytest.mark.parametrize(
    ("result", "payout"),
    [
        ("<div><span>3</span></div><div><span>7</span></div>", "200円"),   # 2 horses, 1 payout
        ("<div><span>3</span></div>", "200円<br />350円"),                  # 1 horse, 2 payouts
        ("<div><span>3</span></div>", "200"),                               # not "N円"
        ("<div><span>三</span></div>", "200円"),                            # not a number
        ("<div><span>3</span></div><div><span>3</span></div>", "200円<br />200円"),  # duplicate
        ("<div><span>3</span></div>", "90円"),                              # below 元返し
    ],
)
def test_unreadable_rows_fail_closed(result, payout):
    with pytest.raises(ParseError):
        parse_win_payouts(_page(result, payout))


def test_conflicting_rows_fail_closed_but_duplicates_are_fine():
    one = _table("<div><span>3</span></div>", "200円")
    other = _table("<div><span>4</span></div>", "500円")
    assert _pairs(f"<html><body>{one}{one}</body></html>") == [(3, 200)]
    with pytest.raises(ParseError):
        parse_win_payouts(f"<html><body>{one}{other}</body></html>")
