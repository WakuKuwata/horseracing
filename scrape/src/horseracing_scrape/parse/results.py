"""結果 (results) parser: real netkeiba result HTML -> ScrapedResult (Feature 022).

Real markup: ``table.RaceTable01`` rows; column order (verified live 2026-06-28):
0 着順 / 1 枠 / 2 馬番 / 3 馬名(+/horse/{id}) / 4 性齢 / 5 斤量 / 6 騎手 / 7 タイム /
8 着差 / 9 人気 / 10 単勝 / 11 後3F / 12 コーナー通過順 / 13 厩舎 / 14 馬体重.
We extract finish_order/status/finish_time + last_3f(後3F) + corner_orders(通過順), and (Feature
139) the FINAL win odds(単勝) + popularity(人気) — what a win bet on this race actually paid at.
finish_time_diff is computed at upsert from per-horse times (interval, JRA-VAN-consistent).

The odds/popularity column positions are taken from the header row when there is one (the cells
line up 1:1 with the ``th``s) and fall back to the verified 10 / 9 only for a header-less table. A
header that does not name both columns yields None for both rather than a guess: these values
OVERWRITE stored odds, so a misread column would be destructive (a None is simply not written).
result_status maps to the enum (finished/stopped/disqualified); 取消/除外 (non-starters) are
skipped. Dead heats share a finish_order. race_id parsed from body (caller re-checks vs URL).
fail-close: ParseError on missing table / unknown status / missing horse id.
"""

from __future__ import annotations

import re

from horseracing_db.enums import ResultStatus

from ..models import NotYetPublished, ParseError, ScrapedResult, ScrapedResultRow
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


#: verified column positions (header 人気 / 単勝オッズ) for a table that carries no header row
_DEFAULT_POPULARITY_COL = 9
_DEFAULT_WIN_ODDS_COL = 10
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _number_or_none(s: str | None) -> str | None:
    """The cell's number as text ("1,234.5" -> "1234.5"), or None for "---" / blank / anything
    else that is not a plain non-negative number."""
    if not s:
        return None
    t = s.replace(",", "").strip()
    return t if _NUMBER_RE.fullmatch(t) else None


def _win_odds(s: str | None) -> float | None:
    t = _number_or_none(s)
    return float(t) if t is not None else None


def _popularity(s: str | None) -> int | None:
    t = _number_or_none(s)
    if t is None or "." in t:
        return None
    n = int(t)
    # 1..99: the page prints a sentinel (seen: "9999") on an excluded horse's row
    return n if 1 <= n <= 99 else None


def _market_columns(table) -> tuple[int | None, int | None]:
    """(popularity, win odds) cell index, read from the header when the table has one."""
    header = table.select_one("tr.Header") or next(
        (tr for tr in table.select("tr") if tr.find("th") is not None), None
    )
    if header is None:
        return _DEFAULT_POPULARITY_COL, _DEFAULT_WIN_ODDS_COL
    labels = ["".join(_text(th).split()) for th in header.find_all("th")]
    popularity = next((i for i, lab in enumerate(labels) if lab == "人気"), None)
    win_odds = next((i for i, lab in enumerate(labels) if lab.startswith("単勝")), None)
    if popularity is None or win_odds is None:
        return None, None  # the layout moved: read neither rather than guess a column
    return popularity, win_odds


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
        raise NotYetPublished("missing required element: table.RaceTable01")

    popularity_col, win_odds_col = _market_columns(table)
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
        win_odds = (_win_odds(cells[win_odds_col])
                    if win_odds_col is not None and len(cells) > win_odds_col else None)
        popularity = (_popularity(cells[popularity_col])
                      if popularity_col is not None and len(cells) > popularity_col else None)
        out.append(
            ScrapedResultRow(
                netkeiba_horse_id=horse_id,
                finish_order=finish_order,
                result_status=status,
                finish_time=finish_time,
                last_3f=last_3f,
                corner_orders=corner_orders,
                win_odds=win_odds,
                popularity=popularity,
            )
        )
    if not out:
        raise ParseError("no result rows")
    return ScrapedResult(key=key, rows=tuple(out))
