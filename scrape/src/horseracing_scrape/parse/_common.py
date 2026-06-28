"""Shared parsing helpers.

Real-netkeiba helpers (Feature 022) live alongside the legacy synthetic-fixture helpers
(``race_key_from`` etc.) which remain only until the results/odds parsers are migrated (US2/US3).
"""

from __future__ import annotations

import datetime
import re

from bs4 import BeautifulSoup

from ..models import ParseError, ScrapedRaceKey

_RACE_ID_RE = re.compile(r"race_id=(\d{12})")
_CANONICAL_RE = re.compile(r'(?:canonical"\s+href|og:url"\s+content)="[^"]*race_id=(\d{12})')
_BAREI_RE = re.compile(r"([牡牝セせ騙])\s*(\d{1,2})")


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# --- real-netkeiba helpers (022) -------------------------------------------

def race_id_from_html(html: str) -> str:
    """Most frequent 12-digit race_id appearing in the page (in race_id=... links).

    fail-close: ParseError if none present.
    """
    # prefer the authoritative canonical/og:url race_id (the day's nav links many other races)
    cm = _CANONICAL_RE.search(html)
    if cm:
        return cm.group(1)
    ids = _RACE_ID_RE.findall(html)
    if not ids:
        raise ParseError("no race_id found in page")
    return max(set(ids), key=ids.count)


def race_key_from_race_id(race_id: str) -> ScrapedRaceKey:
    """Split a JRA-VAN/netkeiba race_id (YYYYVVKKDDRR) into its components."""
    if not (len(race_id) == 12 and race_id.isdigit()):
        raise ParseError(f"malformed race_id: {race_id!r}")
    return ScrapedRaceKey(
        year=int(race_id[0:4]), track_code=race_id[4:6], kai=int(race_id[6:8]),
        nichime=int(race_id[8:10]), race_no=int(race_id[10:12]),
    )


def required_int(value: str | None, what: str) -> int:
    """Strict int parse for required fields — ParseError (not None) on missing/invalid."""
    if value is None or value.strip() == "":
        raise ParseError(f"missing required int: {what}")
    try:
        return int(value.strip())
    except ValueError as e:
        raise ParseError(f"non-int {what}: {value!r}") from e


def parse_sex_age(text: str | None) -> tuple[str | None, int | None]:
    """'牡2' / '牝3' / 'セ4' -> (sex, age). Returns (None, None) if unparseable."""
    if not text:
        return None, None
    m = _BAREI_RE.search(text)
    if not m:
        return None, None
    sex = "セ" if m.group(1) in "セせ騙" else m.group(1)
    return sex, int(m.group(2))


def id_from_href(href: str | None, segment: str) -> str | None:
    """Extract the id following /{segment}/ in a netkeiba href (e.g. /horse/2022103995)."""
    if not href:
        return None
    m = re.search(rf"/{segment}/(?:result/recent/)?(\w+)", href)
    return m.group(1) if m else None


def require(el, name: str):
    if el is None:
        raise ParseError(f"missing required element: {name}")
    return el


def attr(el, name: str, *, required: bool = False) -> str | None:
    val = el.get(name)
    if val is None or val == "":
        if required:
            raise ParseError(f"missing required attribute: {name}")
        return None
    return val


def to_int(val: str | None) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except ValueError:
        return None


def to_float(val: str | None) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def race_key_from(div) -> ScrapedRaceKey:
    year = to_int(attr(div, "data-year", required=True))
    kai = to_int(attr(div, "data-kai", required=True))
    nichime = to_int(attr(div, "data-day", required=True))
    race_no = to_int(attr(div, "data-raceno", required=True))
    track = attr(div, "data-track", required=True)
    if None in (year, kai, nichime, race_no):
        raise ParseError("non-integer race key field")
    return ScrapedRaceKey(year=year, track_code=track, kai=kai, nichime=nichime, race_no=race_no)


def race_date_from(div) -> datetime.date | None:
    raw = attr(div, "data-date")
    if raw is None:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None
