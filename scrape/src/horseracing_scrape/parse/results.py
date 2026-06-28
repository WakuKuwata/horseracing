"""結果 (results) parser: real netkeiba result HTML -> ScrapedResult (Feature 022).

Real markup: ``table.RaceTable01`` rows; columns 着順/枠/馬番/馬名(+/horse/{id})/性齢/斤量/騎手/
タイム(例 "2:00.5")/着差/人気/単勝/後3F. result_status maps to the existing enum
(finished/stopped/disqualified); 取消/除外 (non-starters) are skipped (no result row, handled at
upsert too). Dead heats share a finish_order. race_id parsed from body (caller re-checks vs URL).
fail-close: ParseError on missing table / unknown status / missing horse id.
"""

from __future__ import annotations

from horseracing_db.enums import ResultStatus

from ..models import ParseError, ScrapedResult, ScrapedResultRow
from ._common import id_from_href, race_id_from_html, race_key_from_race_id, soup_of

# 着順セルのテキスト先頭で状態を判定（数字=finished）
_STOPPED = ("中",)          # 中止
_DISQ = ("失",)             # 失格 / 降着扱いは別途
_NON_STARTER = ("除", "取")  # 除外 / 取消 → result 行なし（skip）


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_results(html: str) -> ScrapedResult:
    soup = soup_of(html)
    key = race_key_from_race_id(race_id_from_html(html))

    table = soup.select_one("table.RaceTable01")
    if table is None:
        raise ParseError("missing required element: table.RaceTable01")

    out: list[ScrapedResultRow] = []
    for tr in table.select("tr"):
        link = tr.select_one('a[href*="/horse/"]')
        if link is None:  # header / non-horse row
            continue
        horse_id = id_from_href(link.get("href"), "horse")
        if not horse_id:
            raise ParseError("missing required horse id in result row")
        cells = [_text(td) for td in tr.find_all("td")]
        if len(cells) < 8:
            raise ParseError(f"result row too short: {cells}")
        order_txt = cells[0]
        if order_txt.isdigit():
            status, finish_order = ResultStatus.FINISHED, int(order_txt)
        elif any(c in order_txt for c in _STOPPED):
            status, finish_order = ResultStatus.STOPPED, None
        elif any(c in order_txt for c in _DISQ):
            status, finish_order = ResultStatus.DISQUALIFIED, None
        elif any(c in order_txt for c in _NON_STARTER):
            continue  # 取消/除外 — non-starter, no result row
        else:
            raise ParseError(f"unknown result status in 着順: {order_txt!r}")

        finish_time = cells[7] if len(cells) > 7 and cells[7] else None
        out.append(
            ScrapedResultRow(
                netkeiba_horse_id=horse_id,
                finish_order=finish_order,
                result_status=status,
                finish_time=finish_time,
            )
        )
    if not out:
        raise ParseError("no result rows")
    return ScrapedResult(key=key, rows=tuple(out))
