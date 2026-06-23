"""前売りオッズ (odds) parser: HTML -> ScrapedOdds (単勝オッズ・人気)."""

from __future__ import annotations

from ..models import ParseError, ScrapedOdds, ScrapedOddsRow
from ._common import attr, race_key_from, require, soup_of, to_float, to_int


def parse_odds(html: str) -> ScrapedOdds:
    soup = soup_of(html)
    div = require(soup.select_one("div.race"), "div.race")
    key = race_key_from(div)
    rows = div.select("table.odds tr.horse")
    if not rows:
        raise ParseError("no odds rows")
    out = tuple(
        ScrapedOddsRow(
            netkeiba_horse_id=attr(tr, "data-horse-id", required=True),
            odds=to_float(attr(tr, "data-odds")),
            popularity=to_int(attr(tr, "data-popularity")),
        )
        for tr in rows
    )
    return ScrapedOdds(key=key, rows=out)
