"""Shared helpers for the JRA parsers: race header, numbers, ids.

(contracts/fetch-and-parse.md §3)
"""

from __future__ import annotations

import datetime
import re
import unicodedata

from bs4 import BeautifulSoup, Tag

from ...models import ParseError
from ...venues import build_race_id
from ..identity import HORSE, JOCKEY, TRAINER, official_id
from ..models import JraRaceHeader
from ..venues import venue_code

JST = datetime.timezone(datetime.timedelta(hours=9))

# NFKC turns full-width （） into ASCII parentheses, so match either.
_DATE_RE = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[（(].曜[）)]\s*(\d{1,2})回(\S+?)(\d{1,2})日"
)
_POST_RE = re.compile(r"(\d{1,2})時(\d{2})分")
_RACE_NO_RE = re.compile(r"(\d{1,2})レース")
_COURSE_RE = re.compile(r"([\d,]+)\s*メートル\s*[（(]?\s*(芝|ダート|障害)")
_GRADE_MAP = {"GI": "G1", "GII": "G2", "GIII": "G3", "G1": "G1", "G2": "G2", "G3": "G3"}


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def nfkc(text: str | None) -> str:
    if text is None:
        return ""
    return unicodedata.normalize("NFKC", text).replace("　", " ").strip()


def text_of(el: Tag | None) -> str:
    return nfkc(el.get_text(" ", strip=True)) if el is not None else ""


def to_int(text: str | None) -> int | None:
    t = nfkc(text).replace(",", "")
    m = re.search(r"[-+]?\d+", t)
    return int(m.group(0)) if m else None


def to_float(text: str | None) -> float | None:
    t = nfkc(text).replace(",", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def require(el, what: str) -> Tag:
    if el is None:
        raise ParseError(f"missing required element: {what}")
    return el


def parse_sex_age(text: str | None) -> tuple[str | None, int | None]:
    t = nfkc(text)
    m = re.search(r"(牡|牝|セ|せん|騙)\s*(\d{1,2})", t)
    if not m:
        return None, None
    sex = m.group(1)
    sex = "セ" if sex in ("セ", "せん", "騙") else sex
    return sex, int(m.group(2))


def parse_body_weight(text: str | None) -> tuple[int | None, int | None]:
    """'520(0)' / '474(+2)' / '' → (weight, diff). A bare weight gives (w, None)."""
    t = nfkc(text).replace(" ", "")
    if not t:
        return None, None
    m = re.match(r"^(\d{3})(?:\(([-+]?\d+)\))?$", t)
    if not m:
        return None, None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) is not None else None)


def ids_from_cell(cell: Tag | None, kind: str) -> tuple[str | None, str | None]:
    """(official id or None, display name) from a 馬名/騎手名/調教師名 cell."""
    if cell is None:
        return None, None
    a = cell.find("a")
    name = text_of(a if a is not None else cell) or None
    if a is None:
        return None, name
    href = a.get("href") or ""
    onclick = a.get("onclick") or ""
    return official_id(kind, href) or official_id(kind, onclick), name


def frame_from_cell(cell: Tag | None) -> int | None:
    if cell is None:
        return None
    img = cell.find("img")
    alt = img.get("alt", "") if img is not None else ""
    m = re.search(r"枠\s*(\d{1,2})", nfkc(alt))
    return int(m.group(1)) if m else to_int(text_of(cell))


def parse_race_header(header: Tag, *, expected_date: datetime.date) -> JraRaceHeader:
    """``div.race_header`` → header. Raises ParseError when the date/venue/round do not match."""
    date_txt = text_of(header.select_one(".cell.date"))
    m = _DATE_RE.search(date_txt)
    if not m:
        raise ParseError(f"race header date unreadable: {date_txt!r}")
    y, mo, d, kai, venue_name, nichime = m.groups()
    race_date = datetime.date(int(y), int(mo), int(d))
    if race_date != expected_date:
        raise ParseError(f"race header is for {race_date}, expected {expected_date}")
    vc = venue_code(venue_name)
    if vc is None:
        raise ParseError(f"unknown venue in header: {venue_name!r}")
    no_img = header.select_one(".race_number img")
    rn = _RACE_NO_RE.search(nfkc(no_img.get("alt", "")) if no_img is not None else "")
    if not rn:
        raise ParseError("race number missing in header")
    race_no = int(rn.group(1))
    race_id = build_race_id(
        year=race_date.year, track_code=vc, kai=int(kai), nichime=int(nichime), race_no=race_no
    )
    if race_id is None:
        raise ParseError(f"cannot build race_id for {race_date} {vc} {kai}回{nichime}日 {race_no}R")

    post_time = None
    pm = _POST_RE.search(text_of(header.select_one(".cell.time")))
    if pm:
        post_time = datetime.datetime.combine(
            race_date, datetime.time(int(pm.group(1)), int(pm.group(2))), tzinfo=JST
        )
    distance = track_type = None
    cm = _COURSE_RE.search(text_of(header.select_one(".cell.course")))
    if cm:
        distance = int(cm.group(1).replace(",", ""))
        track_type = cm.group(2)
    weather = going = None
    baba = header.select_one(".cell.baba")
    if baba is not None:
        w = baba.select_one("li.weather .txt")
        weather = text_of(w) or None
        g = baba.select_one("li.turf .txt, li.durt .txt, li.dirt .txt")
        going = text_of(g) or None
    grade = None
    gi = header.select_one(".grade_icon img")
    if gi is not None:
        grade = _GRADE_MAP.get(nfkc(gi.get("alt", "")).replace(" ", "").upper())
    prize = None
    for li in header.select("ul.prize ol li"):
        if "1着" in text_of(li.select_one(".cap")):
            prize = to_int(text_of(li.select_one(".num")))
            break
    return JraRaceHeader(
        race_id=race_id,
        race_date=race_date,
        venue_code=vc,
        kai=int(kai),
        nichime=int(nichime),
        race_no=race_no,
        race_name=text_of(header.select_one(".race_name")) or None,
        post_time=post_time,
        distance=distance,
        track_type=track_type,
        weather=weather,
        going=going,
        race_class=text_of(header.select_one(".cell.class")) or None,
        grade=grade,
        prize_money=prize,
    )


def require_columns(table: Tag, required: tuple[str, ...], *, what: str) -> None:
    """The thead must carry every required column class — a changed layout fails closed."""
    classes: set[str] = set()
    for th in table.select("thead th"):
        classes.update(th.get("class") or [])
    missing = [c for c in required if c not in classes]
    if missing:
        raise ParseError(f"{what}: missing columns {missing}")


def hhmm_on(date: datetime.date, text: str) -> datetime.datetime | None:
    m = _POST_RE.search(nfkc(text))
    if not m:
        return None
    return datetime.datetime.combine(
        date, datetime.time(int(m.group(1)), int(m.group(2))), tzinfo=JST
    )


__all__ = [
    "HORSE",
    "JOCKEY",
    "TRAINER",
    "JST",
    "frame_from_cell",
    "hhmm_on",
    "ids_from_cell",
    "nfkc",
    "parse_body_weight",
    "parse_race_header",
    "parse_sex_age",
    "require",
    "require_columns",
    "soup",
    "text_of",
    "to_float",
    "to_int",
]
