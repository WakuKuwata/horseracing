"""出馬表 (entries) parser: real netkeiba shutuba HTML -> ScrapedEntry (Feature 022).

Real markup: ``table.Shutuba_Table`` rows ``tr.HorseList`` with cells
Waku* (枠) / Umaban* (馬番) / HorseInfo (馬名 + /horse/{id}) / Barei (性齢) / Weight (馬体重(増減))
/ Jockey (/jockey/.../{id}) / Trainer (区 + /trainer/.../{id}). Race meta from
``RaceData01`` (発走/距離/馬場/天候), ``RaceData02`` (回/場/日次/クラス/頭数), <title> (開催日).

weight = 馬体重 (body weight) to match JRA-VAN ``race_horses.weight``; 斤量 (jockey_weight) and
増減 (weight_diff) are NOT carried by ScrapedEntryHorse (deferred — needs a model/upsert change).
Pre-race upcoming races show no body weight ("計不") -> weight is None (Unknown, constitution IV).
fail-close: ParseError on missing table / required fields. race_id parsed from body; the caller
re-checks it against the URL race_id.
"""

from __future__ import annotations

import datetime
import re

from ..models import ParseError, ScrapedEntry, ScrapedEntryHorse, ScrapedRace
from ._common import (
    id_from_href,
    parse_sex_age,
    race_id_from_html,
    race_key_from_race_id,
    required_int,
    soup_of,
)

_DIST_RE = re.compile(r"(芝|ダ|障)(\d{3,4})m")
_WEATHER_RE = re.compile(r"天候\s*[:：]\s*(\S+)")
_GOING_RE = re.compile(r"馬場\s*[:：]\s*(\S+)")
_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_BODYWEIGHT_RE = re.compile(r"(\d{3})")
_CLASS_TOKENS = ("新馬", "未勝利", "３勝", "3勝", "２勝", "2勝", "１勝", "1勝",
                 "オープン", "Ｇ", "G1", "G2", "G3")


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def _race_meta(soup, key) -> ScrapedRace:
    rd01 = _text(soup.select_one(".RaceData01"))
    rd02 = _text(soup.select_one(".RaceData02"))
    title = _text(soup.select_one("title"))

    track_type = distance = None
    m = _DIST_RE.search(rd01)
    if m:
        track_type, distance = m.group(1), int(m.group(2))
    wm = _WEATHER_RE.search(rd01)
    gm = _GOING_RE.search(rd01)
    weather = wm.group(1) if wm else None
    going = gm.group(1) if gm else None

    race_date = None
    dm = _DATE_RE.search(title)
    if dm:
        race_date = datetime.date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))

    race_class = next((t for t in _CLASS_TOKENS if t in rd02), None)

    return ScrapedRace(
        key=key, race_date=race_date, distance=distance, track_type=track_type,
        going=going, weather=weather, race_class=race_class,
    )


def _entry_status(row) -> str:
    txt = row.get_text(" ", strip=True)
    if "取消" in txt or "除外" in txt:
        return "cancelled"
    return "started"


def _body_weight(cell_text: str) -> int | None:
    m = _BODYWEIGHT_RE.search(cell_text)  # "484 (0)" -> 484 ; "計不"/"--" -> None
    return int(m.group(1)) if m else None


def parse_entries(html: str) -> ScrapedEntry:
    soup = soup_of(html)
    race_id = race_id_from_html(html)
    key = race_key_from_race_id(race_id)

    table = soup.select_one("table.Shutuba_Table")
    if table is None:
        raise ParseError("missing required element: table.Shutuba_Table")
    rows = table.select("tr.HorseList")
    if not rows:
        raise ParseError("no entry rows (tr.HorseList)")

    race = _race_meta(soup, key)
    horses: list[ScrapedEntryHorse] = []
    for row in rows:
        info = row.select_one("td.HorseInfo")
        horse_link = info.find("a", href=True) if info else None
        netkeiba_horse_id = id_from_href(horse_link["href"] if horse_link else None, "horse")
        if not netkeiba_horse_id:
            raise ParseError("missing required horse id (/horse/{id})")

        waku = row.select_one('td[class*="Waku"]')
        umaban = row.select_one('td[class*="Umaban"]')
        horse_number = required_int(_text(umaban), "horse_number")
        frame = int(_text(waku)) if waku and _text(waku).isdigit() else None

        sex, age = parse_sex_age(_text(row.select_one("td.Barei")))
        weight = _body_weight(_text(row.select_one("td.Weight")))

        jockey = row.select_one("td.Jockey")
        jockey_link = jockey.find("a", href=True) if jockey else None
        trainer = row.select_one("td.Trainer")
        trainer_link = trainer.find("a", href=True) if trainer else None
        trainer_name = re.sub(r"^(栗東|美浦|地方|海外)\s*", "", _text(trainer)) or None

        horses.append(
            ScrapedEntryHorse(
                netkeiba_horse_id=netkeiba_horse_id,
                horse_name=(_text(info).split()[0] if info and _text(info) else None),
                frame=frame,
                horse_number=horse_number,
                netkeiba_jockey_id=id_from_href(
                    jockey_link["href"] if jockey_link else None, "jockey"),
                jockey_name=_text(jockey) or None,
                netkeiba_trainer_id=id_from_href(
                    trainer_link["href"] if trainer_link else None, "trainer"),
                trainer_name=trainer_name,
                weight=weight,
                sex=sex,
                age=age,
                entry_status=_entry_status(row),
            )
        )

    numbers = [h.horse_number for h in horses]
    if len(set(numbers)) != len(numbers):
        raise ParseError("duplicate horse_number within race")

    return ScrapedEntry(race=race, horses=tuple(horses))
