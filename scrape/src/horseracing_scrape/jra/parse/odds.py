"""単複オッズ(1 レース 1 ページ)の解析(Feature 176, FR-013/FR-019)."""

from __future__ import annotations

import datetime
import re

from horseracing_db.enums import EntryStatus

from ...models import ParseError
from ..models import JraWinOdds, JraWinOddsRow
from ._common import (
    HORSE,
    JST,
    ids_from_cell,
    parse_body_weight,
    parse_race_header,
    require_columns,
    soup,
    text_of,
    to_float,
    to_int,
)

REQUIRED_COLUMNS = ("num", "horse", "odds_tan", "odds_fuku", "h_weight", "weight")
_TIME_RE = re.compile(r"(\d{1,2})時(\d{2})分\s*現在")


def parse_win_odds(html: str, *, expected_date: datetime.date) -> JraWinOdds:
    doc = soup(html)
    header = doc.select_one(".race_header")
    if header is None:
        raise ParseError("odds page: race_header missing")
    race = parse_race_header(header, expected_date=expected_date)
    stamp = text_of(doc.select_one(".refresh_line .cell.time"))
    if "前日最終" in stamp:
        kind, official_at = "prev_day_final", None
    else:
        m = _TIME_RE.search(stamp)
        if not m:
            raise ParseError(
                f"odds page {race.race_id}: 「HH時MM分現在オッズ」 missing ({stamp!r})"
            )
        kind = "current"
        official_at = datetime.datetime.combine(
            expected_date, datetime.time(int(m.group(1)), int(m.group(2))), tzinfo=JST
        )
    table = doc.select_one("table.tanpuku")
    if table is None:
        raise ParseError(f"odds page {race.race_id}: 単複 table missing")
    require_columns(table, REQUIRED_COLUMNS, what=f"odds {race.race_id}")
    rows: list[JraWinOddsRow] = []
    for tr in table.select("tbody tr"):
        num_cell = tr.select_one("td.num")
        horse_cell = tr.select_one("td.horse")
        if num_cell is None or horse_cell is None:
            raise ParseError(f"odds {race.race_id}: row without 馬番/馬名")
        num_txt = text_of(num_cell)
        tan_txt = text_of(tr.select_one("td.odds_tan"))
        status = EntryStatus.STARTED
        if "取消" in num_txt or "取消" in tan_txt:
            status = EntryStatus.CANCELLED
        elif "除外" in num_txt or "除外" in tan_txt:
            status = EntryStatus.EXCLUDED
        number = to_int(num_txt)
        horse_id, horse_name = ids_from_cell(horse_cell, HORSE)
        fuku = tr.select_one("td.odds_fuku")
        low = to_float(text_of(fuku.select_one(".min"))) if fuku is not None else None
        high = to_float(text_of(fuku.select_one(".max"))) if fuku is not None else None
        if (low is None) != (high is None):
            low = high = None  # a half range is not a quote
        weight, diff = parse_body_weight(text_of(tr.select_one("td.h_weight")))
        rows.append(
            JraWinOddsRow(
                horse_number=number,
                horse_id=horse_id,
                horse_name=horse_name,
                win_odds=to_float(tan_txt) if status == EntryStatus.STARTED else None,
                place_low=low if status == EntryStatus.STARTED else None,
                place_high=high if status == EntryStatus.STARTED else None,
                weight=weight,
                weight_diff=diff,
                jockey_weight=to_float(text_of(tr.select_one("td.weight"))),
                entry_status=status,
            )
        )
    if not rows:
        raise ParseError(f"odds {race.race_id}: no rows")
    return JraWinOdds(race_id=race.race_id, kind=kind, official_at=official_at, rows=tuple(rows))
