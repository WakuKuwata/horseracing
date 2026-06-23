"""Shared parsing helpers. Fixtures use a controlled data-* attribute shape."""

from __future__ import annotations

import datetime

from bs4 import BeautifulSoup

from ..models import ParseError, ScrapedRaceKey


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


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
