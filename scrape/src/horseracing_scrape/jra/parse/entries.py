"""出馬表(開催日 1 ページ・全レース)の解析(Feature 176, FR-008/FR-012)."""

from __future__ import annotations

import datetime

from bs4 import Tag
from horseracing_db.enums import EntryStatus

from ...models import ParseError
from ..models import JraDayPage, JraEntry, JraEntryHorse, JraRaceBlock
from ._common import (
    HORSE,
    JOCKEY,
    TRAINER,
    frame_from_cell,
    ids_from_cell,
    parse_body_weight,
    parse_race_header,
    parse_sex_age,
    require_columns,
    soup,
    text_of,
    to_float,
    to_int,
)

REQUIRED_COLUMNS = ("waku", "num", "horse", "h_weight", "age", "weight", "jockey", "trainer")


def _status_and_number(cell: Tag) -> tuple[str, int | None]:
    t = text_of(cell)
    if "取消" in t:
        return EntryStatus.CANCELLED, None
    if "除外" in t:
        return EntryStatus.EXCLUDED, None
    n = to_int(t)
    if n is None:
        raise ParseError(f"馬番 unreadable: {t!r}")
    return EntryStatus.STARTED, n


def _parse_block(table: Tag, *, expected_date: datetime.date) -> JraEntry:
    header = table.select_one(".race_header")
    if header is None:
        raise ParseError("race_header missing")
    race = parse_race_header(header, expected_date=expected_date)
    require_columns(table, REQUIRED_COLUMNS, what=f"entries {race.race_id}")
    horses = []
    for tr in table.select("tbody tr"):
        cells = {c: tr.select_one(f"td.{c}") for c in REQUIRED_COLUMNS}
        if cells["num"] is None or cells["horse"] is None:
            raise ParseError(f"entries {race.race_id}: row without 馬番/馬名 cell")
        status, number = _status_and_number(cells["num"])
        horse_id, horse_name = ids_from_cell(cells["horse"], HORSE)
        jockey_id, jockey_name = ids_from_cell(cells["jockey"], JOCKEY)
        trainer_id, trainer_name = ids_from_cell(cells["trainer"], TRAINER)
        unknown = tuple(
            k
            for k, v in (("horse", horse_id), ("jockey", jockey_id), ("trainer", trainer_id))
            if v is None
        )
        weight, diff = parse_body_weight(text_of(cells["h_weight"]))
        sex, age = parse_sex_age(text_of(cells["age"]))
        horses.append(
            JraEntryHorse(
                horse_id=horse_id,
                horse_name=horse_name,
                frame=frame_from_cell(cells["waku"]),
                horse_number=number,
                jockey_id=jockey_id,
                jockey_name=jockey_name,
                trainer_id=trainer_id,
                trainer_name=trainer_name,
                weight=weight,
                weight_diff=diff,
                jockey_weight=to_float(text_of(cells["weight"])),
                sex=sex,
                age=age,
                entry_status=status,
                unknown_id_kinds=unknown,
            )
        )
    if not horses:
        raise ParseError(f"entries {race.race_id}: no horse rows")
    return JraEntry(race=race, horses=tuple(horses))


def parse_entries_day(html: str, *, expected_date: datetime.date) -> JraDayPage[JraEntry]:
    """One venue's 出馬表 page (all races). Each race is judged on its own (FR-008)."""
    doc = soup(html)
    tables = [t for t in doc.select("table.basic") if t.select_one(".race_header") is not None]
    if not tables:
        raise ParseError("entries page: no race tables")
    blocks: list[JraRaceBlock[JraEntry]] = []
    venue = kai = nichime = None
    for i, table in enumerate(tables, start=1):
        try:
            entry = _parse_block(table, expected_date=expected_date)
        except ParseError as exc:
            blocks.append(
                JraRaceBlock(race_no=i, race_id=None, state="parse_failed", error=str(exc))
            )
            continue
        r = entry.race
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
            JraRaceBlock(race_no=r.race_no, race_id=r.race_id, state="published", payload=entry)
        )
    if venue is None:
        raise ParseError("entries page: every race block failed to parse")
    ids = [b.race_id for b in blocks if b.race_id]
    if len(ids) != len(set(ids)):
        raise ParseError("entries page: duplicate race_id across blocks")
    return JraDayPage(
        race_date=expected_date, venue_code=venue, kai=kai, nichime=nichime, races=tuple(blocks)
    )
