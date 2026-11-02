"""レース結果(開催日 1 ページ・全レース)の解析(Feature 176, FR-014/FR-019).

人気(``td.pop``)は読むが、監査の比較材料であって ``race_horses.popularity`` には書かない(利用者決定
2026-11-02・research D9)。
"""

from __future__ import annotations

import datetime
import re

from bs4 import Tag
from horseracing_db.enums import EntryStatus, ResultStatus

from ...models import ParseError
from ..models import JraDayPage, JraRaceBlock, JraRaceResult, JraResultRow
from ._common import (
    HORSE,
    JOCKEY,
    TRAINER,
    frame_from_cell,
    ids_from_cell,
    nfkc,
    parse_body_weight,
    parse_race_header,
    parse_sex_age,
    require_columns,
    soup,
    text_of,
    to_float,
    to_int,
)
from .payouts import parse_refund_block

REQUIRED_COLUMNS = (
    "place",
    "num",
    "horse",
    "age",
    "weight",
    "jockey",
    "time",
    "margin",
    "corner",
    "f_time",
    "h_weight",
    "trainer",
    "pop",
)
_STATUS_WORDS = {
    "中止": ResultStatus.STOPPED,
    "失格": ResultStatus.DISQUALIFIED,
    "取消": EntryStatus.CANCELLED,
    "除外": EntryStatus.EXCLUDED,
}
_LAP_SPLIT_RE = re.compile(r"\s*-\s*")
_UP_RE = re.compile(r"4F\s*([\d.]+)\s*-\s*3F\s*([\d.]+)")


def _place(cell: Tag) -> tuple[int | None, str]:
    t = text_of(cell)
    for word, status in _STATUS_WORDS.items():
        if word in t:
            return None, status
    n = to_int(t)
    if n is None:
        raise ParseError(f"着順 unreadable: {t!r}")
    return n, ResultStatus.FINISHED


def _corner_orders(cell: Tag | None) -> tuple[str, ...] | None:
    if cell is None:
        return None
    items = [text_of(li) for li in cell.select("li")]
    items = [i for i in items if i]
    return tuple(items) if items else None


def _parse_rows(table: Tag, race_id: str) -> tuple[JraResultRow, ...]:
    require_columns(table, REQUIRED_COLUMNS, what=f"results {race_id}")
    rows: list[JraResultRow] = []
    for tr in table.select("tbody tr"):
        cells = {c: tr.select_one(f"td.{c}") for c in REQUIRED_COLUMNS}
        if cells["place"] is None or cells["horse"] is None:
            raise ParseError(f"results {race_id}: row without 着順/馬名")
        finish_order, status = _place(cells["place"])
        horse_id, horse_name = ids_from_cell(cells["horse"], HORSE)
        jockey_id, jockey_name = ids_from_cell(cells["jockey"], JOCKEY)
        trainer_id, trainer_name = ids_from_cell(cells["trainer"], TRAINER)
        weight, diff = parse_body_weight(text_of(cells["h_weight"]))
        sex, age = parse_sex_age(text_of(cells["age"]))
        time_txt = text_of(cells["time"]) or None
        margin = text_of(cells["margin"]) or None
        if status == ResultStatus.FINISHED and finish_order is not None and finish_order == 1:
            margin = None  # the winner has no margin
        rows.append(
            JraResultRow(
                horse_id=horse_id,
                horse_name=horse_name,
                horse_number=to_int(text_of(cells["num"])),
                frame=frame_from_cell(tr.select_one("td.waku")),
                finish_order=finish_order,
                result_status=status,
                finish_time=time_txt if status == ResultStatus.FINISHED else None,
                margin=margin,
                corner_orders=_corner_orders(cells["corner"]),
                last_3f=to_float(text_of(cells["f_time"])),
                weight=weight,
                weight_diff=diff,
                jockey_id=jockey_id,
                jockey_name=jockey_name,
                trainer_id=trainer_id,
                trainer_name=trainer_name,
                jockey_weight=to_float(text_of(cells["weight"])),
                sex=sex,
                age=age,
                popularity=to_int(text_of(cells["pop"])),
                unknown_id_kinds=tuple(
                    k
                    for k, v in (
                        ("horse", horse_id),
                        ("jockey", jockey_id),
                        ("trainer", trainer_id),
                    )
                    if v is None
                ),
            )
        )
    if not rows:
        raise ParseError(f"results {race_id}: no rows")
    finished = [r for r in rows if r.result_status == ResultStatus.FINISHED]
    if finished and any(r.finish_order is None or r.finish_time is None for r in finished):
        raise ParseError(f"results {race_id}: finished row without 着順/タイム")
    return tuple(rows)


def _laps(unit: Tag) -> tuple[tuple[float, ...], float | None, float | None]:
    laps: tuple[float, ...] = ()
    up4 = up3 = None
    for tr in unit.select("tr"):
        th = text_of(tr.select_one("th"))
        td = text_of(tr.select_one("td"))
        if th == "ハロンタイム" and td:
            try:
                laps = tuple(float(x) for x in _LAP_SPLIT_RE.split(td) if x)
            except ValueError as exc:
                raise ParseError(f"ハロンタイム unreadable: {td!r}") from exc
        elif th == "上り" and td:
            m = _UP_RE.search(nfkc(td))
            if m:
                up4, up3 = float(m.group(1)), float(m.group(2))
            else:
                m3 = re.search(r"3F\s*([\d.]+)", nfkc(td))
                up3 = float(m3.group(1)) if m3 else None
    return laps, up4, up3


def _corner_passing(unit: Tag) -> dict[str, str]:
    out: dict[str, str] = {}
    for tr in unit.select(".result_corner_place tr"):
        th = text_of(tr.select_one("th"))
        td = tr.select_one("td")
        if th and td is not None:
            out[th] = nfkc(td.get_text("", strip=True))
    return out


def _parse_unit(unit: Tag, *, expected_date: datetime.date) -> JraRaceResult:
    header = unit.select_one(".race_header")
    if header is None:
        raise ParseError("race_header missing")
    race = parse_race_header(header, expected_date=expected_date)
    table = next(
        (t for t in unit.select("table") if t.select_one("thead th.place") is not None), None
    )
    if table is None:
        raise ParseError(f"results {race.race_id}: result table missing")
    rows = _parse_rows(table, race.race_id)
    laps, up4, up3 = _laps(unit)
    return JraRaceResult(
        race=race,
        rows=rows,
        lap_times=laps,
        pace_last_4f=up4,
        pace_last_3f=up3,
        corner_passing=_corner_passing(unit),
        payouts=parse_refund_block(unit.select_one(".refund_unit")),
    )


def parse_results_day(html: str, *, expected_date: datetime.date) -> JraDayPage[JraRaceResult]:
    doc = soup(html)
    units = doc.select("div.race_result_unit")
    if not units:
        raise ParseError("results page: no race units")
    blocks: list[JraRaceBlock[JraRaceResult]] = []
    venue = kai = nichime = None
    for i, unit in enumerate(units, start=1):
        if (
            unit.select_one("thead th.place") is None
            and unit.select_one(".race_header") is not None
        ):
            # header but no result table yet → the race has not run (or results not published)
            try:
                race = parse_race_header(
                    unit.select_one(".race_header"), expected_date=expected_date
                )
                blocks.append(
                    JraRaceBlock(race_no=race.race_no, race_id=race.race_id, state="not_yet")
                )
            except ParseError as exc:
                blocks.append(
                    JraRaceBlock(race_no=i, race_id=None, state="parse_failed", error=str(exc))
                )
            continue
        try:
            result = _parse_unit(unit, expected_date=expected_date)
        except ParseError as exc:
            blocks.append(
                JraRaceBlock(race_no=i, race_id=None, state="parse_failed", error=str(exc))
            )
            continue
        r = result.race
        if venue is None:
            venue, kai, nichime = r.venue_code, r.kai, r.nichime
        elif (r.venue_code, r.kai, r.nichime) != (venue, kai, nichime):
            blocks.append(
                JraRaceBlock(
                    race_no=r.race_no,
                    race_id=None,
                    state="parse_failed",
                    error="venue/kai/nichime differ from the page's first race",
                )
            )
            continue
        blocks.append(
            JraRaceBlock(race_no=r.race_no, race_id=r.race_id, state="published", payload=result)
        )
    if venue is None:
        raise ParseError("results page: every race block failed to parse")
    ids = [b.race_id for b in blocks if b.race_id]
    if len(ids) != len(set(ids)):
        raise ParseError("results page: duplicate race_id across blocks")
    return JraDayPage(
        race_date=expected_date, venue_code=venue, kai=kai, nichime=nichime, races=tuple(blocks)
    )
