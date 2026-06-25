"""Exotic (combination-bet) odds parser: HTML -> ScrapedExoticOdds (Feature 012).

Parses the 6 exotic bet-type grids (複勝/馬連/馬単/ワイド/三連複/三連単) into horse_number
combinations + dividend odds. Combinations are keyed by 馬番 (race-local horse numbers, identical
across sources) — NO horse-entity id-mapping is needed (FR-003 holds vacuously: no guess-join).
Network-free: operates on already-fetched HTML; never reads results. Fail-close on a missing race
block; an empty/garbled bet-type table contributes no rows (partial coverage handled downstream).
"""

from __future__ import annotations

from ..models import ParseError, ScrapedExoticOdds, ScrapedExoticRow
from ._common import attr, race_key_from, require, soup_of, to_float

#: netkeiba bet-type labels (data attribute) accepted by the parser.
_EXOTIC_BET_TYPES = frozenset(
    {"place", "quinella", "exacta", "wide", "trio", "trifecta"}
)


def _numbers(raw: str | None) -> tuple[int, ...] | None:
    if not raw:
        return None
    try:
        return tuple(int(part) for part in raw.split("-"))
    except ValueError:
        return None


def parse_exotic_odds(html: str) -> ScrapedExoticOdds:
    soup = soup_of(html)
    div = require(soup.select_one("div.race"), "div.race")
    key = race_key_from(div)

    rows: list[ScrapedExoticRow] = []
    for table in div.select("table.exotic"):
        bet_type = attr(table, "data-bet-type", required=True)
        if bet_type not in _EXOTIC_BET_TYPES:
            continue  # ignore unknown/unsupported grids (e.g. 枠連)
        for tr in table.select("tr.combo"):
            numbers = _numbers(attr(tr, "data-horses"))
            if numbers is None:
                continue
            rows.append(
                ScrapedExoticRow(
                    bet_type=bet_type, numbers=numbers, odds=to_float(attr(tr, "data-odds"))
                )
            )
    if not rows:
        raise ParseError("no exotic odds rows")
    return ScrapedExoticOdds(key=key, rows=tuple(rows))
