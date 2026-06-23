"""結果 (results) parser: HTML -> ScrapedResult (着順・結果状態・タイム).

Result status maps to the existing enum (finished/stopped/disqualified); dead heats share a
finish_order; non-starters carry no result row (handled at upsert).
"""

from __future__ import annotations

from horseracing_db.enums import ResultStatus

from ..models import ParseError, ScrapedResult, ScrapedResultRow
from ._common import attr, race_key_from, require, soup_of, to_int

_STATUS_MAP = {
    "finished": ResultStatus.FINISHED,
    "stopped": ResultStatus.STOPPED,
    "disqualified": ResultStatus.DISQUALIFIED,
}


def parse_results(html: str) -> ScrapedResult:
    soup = soup_of(html)
    div = require(soup.select_one("div.race"), "div.race")
    key = race_key_from(div)
    rows = div.select("table.results tr.horse")
    if not rows:
        raise ParseError("no result rows")
    out = []
    for tr in rows:
        raw_status = (attr(tr, "data-status") or "finished").lower()
        status = _STATUS_MAP.get(raw_status)
        if status is None:
            raise ParseError(f"unknown result status: {raw_status!r}")
        out.append(
            ScrapedResultRow(
                netkeiba_horse_id=attr(tr, "data-horse-id", required=True),
                finish_order=to_int(attr(tr, "data-finish")),
                result_status=status,
                finish_time=attr(tr, "data-time"),
            )
        )
    return ScrapedResult(key=key, rows=tuple(out))
