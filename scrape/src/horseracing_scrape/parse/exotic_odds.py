"""Parse exotic payouts from a real netkeiba result page."""

from __future__ import annotations

import re

from ..models import ParseError, ScrapedExoticOdds, ScrapedExoticRow
from ._common import race_id_from_html, race_key_from_race_id, soup_of

_BET_TYPE_BY_LABEL = {
    "複勝": "place",
    "馬連": "quinella",
    "ワイド": "wide",
    "馬単": "exacta",
    "3連複": "trio",
    "3連単": "trifecta",
}

_SELECTION_SIZE = {
    "place": 1,
    "quinella": 2,
    "wide": 2,
    "exacta": 2,
    "trio": 3,
    "trifecta": 3,
}

_PAYOUT_RE = re.compile(r"^([\d,]+)円$")


def _number(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def _place_selections(result_cell) -> list[tuple[int, ...]] | None:
    selections: list[tuple[int, ...]] = []
    for span in result_cell.select("div span"):
        text = span.get_text(strip=True)
        if not text:
            continue
        number = _number(text)
        if number is None:
            return None
        selections.append((number,))
    return selections


def _combo_selections(result_cell) -> list[tuple[int, ...]] | None:
    selections: list[tuple[int, ...]] = []
    for ul in result_cell.select("ul"):
        numbers: list[int] = []
        for span in ul.select("li span"):
            text = span.get_text(strip=True)
            if not text:
                continue
            number = _number(text)
            if number is None:
                return None
            numbers.append(number)
        if numbers:
            selections.append(tuple(numbers))
    return selections


def _payouts(payout_cell) -> list[float] | None:
    span = payout_cell.select_one("span")
    if span is None:
        return None

    payouts: list[float] = []
    for text in span.stripped_strings:
        match = _PAYOUT_RE.fullmatch(text)
        if match is None:
            return None
        payouts.append(int(match.group(1).replace(",", "")) / 100.0)
    return payouts


def _parse_row(tr, bet_type: str) -> list[ScrapedExoticRow] | None:
    result_cell = tr.select_one("td.Result")
    payout_cell = tr.select_one("td.Payout")
    if result_cell is None or payout_cell is None:
        return None

    if bet_type == "place":
        selections = _place_selections(result_cell)
    else:
        selections = _combo_selections(result_cell)
    payouts = _payouts(payout_cell)
    if selections is None or payouts is None:
        return None

    if len(selections) != len(payouts):
        raise ParseError(
            f"{bet_type} selection/payout count mismatch: "
            f"{len(selections)} selections, {len(payouts)} payouts"
        )

    expected_size = _SELECTION_SIZE[bet_type]
    if not selections or any(len(numbers) != expected_size for numbers in selections):
        return None

    return [
        ScrapedExoticRow(bet_type=bet_type, numbers=numbers, odds=odds)
        for numbers, odds in zip(selections, payouts, strict=True)
    ]


def parse_exotic_odds(html: str) -> ScrapedExoticOdds:
    soup = soup_of(html)
    tables = soup.select("table.Payout_Detail_Table")
    if not tables:
        raise ParseError("no payout table")

    rows: list[ScrapedExoticRow] = []
    for table in tables:
        for tr in table.select("tr"):
            label_cell = tr.select_one("th")
            if label_cell is None:
                continue
            label = label_cell.get_text(strip=True)
            bet_type = _BET_TYPE_BY_LABEL.get(label)
            if bet_type is None:
                continue
            parsed = _parse_row(tr, bet_type)
            if parsed is not None:
                rows.extend(parsed)

    if not rows:
        raise ParseError("no exotic odds rows")

    key = race_key_from_race_id(race_id_from_html(html))
    return ScrapedExoticOdds(key=key, rows=tuple(rows))
