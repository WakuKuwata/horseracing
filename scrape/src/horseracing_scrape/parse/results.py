"""結果 (results) parser: real netkeiba result HTML -> ScrapedResult (Feature 022).

Real markup: ``table.RaceTable01`` rows; column order (verified live 2026-06-28):
0 着順 / 1 枠 / 2 馬番 / 3 馬名(+/horse/{id}) / 4 性齢 / 5 斤量 / 6 騎手 / 7 タイム /
8 着差 / 9 人気 / 10 単勝 / 11 後3F / 12 コーナー通過順 / 13 厩舎 / 14 馬体重.
We extract finish_order/status/finish_time + last_3f(後3F) + corner_orders(通過順);
finish_time_diff is computed at upsert from per-horse times (interval, JRA-VAN-consistent).
result_status maps to the enum (finished/stopped/disqualified); 取消/除外 (non-starters) are
skipped. Dead heats share a finish_order. race_id parsed from body (caller re-checks vs URL).
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


def _to_float(s: str | None) -> float | None:
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _corner_orders(s: str | None) -> tuple[str, ...] | None:
    """"7-7-4-3" -> ("7","7","4","3"); empty/"-" -> None (matches JRA-VAN _corner_orders shape)."""
    if not s:
        return None
    parts = [p.strip() for p in s.split("-") if p.strip() and p.strip() != "0"]
    return tuple(parts) or None


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
        last_3f = _to_float(cells[11]) if len(cells) > 11 else None
        corner_orders = _corner_orders(cells[12]) if len(cells) > 12 else None
        out.append(
            ScrapedResultRow(
                netkeiba_horse_id=horse_id,
                finish_order=finish_order,
                result_status=status,
                finish_time=finish_time,
                last_3f=last_3f,
                corner_orders=corner_orders,
            )
        )
    if not out:
        raise ParseError("no result rows")
    return ScrapedResult(key=key, rows=tuple(out))
