"""Deterministic ID mapping (Feature 178, T009; contracts/files-and-ids.md §3).

* レースキー 8 文字 → ``race_id`` 12 桁 ``YYYYVVKKDDRR`` (JRA venues 01〜10 only; others → None).
* 血統登録番号 8 桁 → ``horse_id`` 10 桁 (century resolved from the race year, or taken from the
  UKC birth date when it is known).
* 騎手/調教師コード are JRDB-private: NOT mapped here (``persons.py`` builds ``id_mappings``).

Both entry points validate the ASCII grammar of their input first; error messages carry no
input bytes (codex D20 #7, #11, #12).
"""

from __future__ import annotations

import datetime
import re

from .errors import JrdbDecodeError
from .models import JRA_VENUE_CODES, JrdbRaceKey

_KEY_RE = re.compile(r"^(\d{2})(\d{2})(\d)([1-9a-f])(\d{2})$")
_BLOOD_RE = re.compile(r"^\d{8}$")

#: a racehorse is 2〜(about) 12 years old; 30 keeps a margin for odd master rows
MAX_HORSE_AGE_YEARS = 30


def key_from_text(key8: str) -> JrdbRaceKey:
    """Parse ``場 2・年 2・回 1・日 1 (16 進 1〜f)・Ｒ 2``; anything else is a decode error."""

    m = _KEY_RE.match(key8.strip(" ").lower())
    if not m:
        raise JrdbDecodeError("race key does not match 99 99 9 F 99")
    venue, yy, kai, day_hex, r = m.groups()
    return JrdbRaceKey(venue=venue, yy=int(yy), kai=int(kai), day_hex=day_hex, r=int(r))


def resolve_year(yy: int, *, reference_year: int | None) -> int:
    """2 桁の年 → 4 桁.

    With a reference year (the file's data date, or the row's own YYYYMMDD) the result is the
    year ≡ ``yy`` (mod 100) nearest to it; a distance of more than one year is an error (year-end
    files may hold the neighbouring year, nothing else). Without a reference the contract's
    fallback applies: ``yy < 90`` → 2000s, else 1900s (JRDB data starts in 1999).
    """

    if not 0 <= yy <= 99:
        raise JrdbDecodeError("yy out of range")
    if reference_year is None:
        return 2000 + yy if yy < 90 else 1900 + yy
    base = reference_year - reference_year % 100 + yy
    candidates = (base - 100, base, base + 100)
    year = min(candidates, key=lambda y: abs(y - reference_year))
    if abs(year - reference_year) > 1:
        raise JrdbDecodeError("race key year does not match the file year")
    return year


def race_id_from_key(key: JrdbRaceKey | str, *, file_year: int | None = None) -> str | None:
    """Return the 12-digit ``race_id`` or ``None`` when the venue is not a JRA course.

    ``file_year`` is the reference for the century (see :func:`resolve_year`). The key's other
    parts are range-checked (回 1〜9, 日 1〜15, Ｒ 1〜12).
    """

    k = key if isinstance(key, JrdbRaceKey) else key_from_text(key)
    if k.venue not in JRA_VENUE_CODES:
        return None
    year = resolve_year(k.yy, reference_year=file_year)
    day = int(k.day_hex, 16)  # JrdbRaceKey validated the single hex digit
    if not 1 <= k.kai <= 9 or not 1 <= day <= 15 or not 1 <= k.r <= 12:
        raise JrdbDecodeError("race key out of range")
    return f"{year:04d}{k.venue}{k.kai:02d}{day:02d}{k.r:02d}"


def horse_id_from_blood_no(
    no8: str, *, race_year: int, birth_date: datetime.date | None = None
) -> str:
    """血統登録番号 (生年 2 桁 + 6 桁) → ``horse_id`` (生年 4 桁 + 6 桁).

    With the UKC birth date the birth year IS that date's year (its last two digits must equal
    the number's first two). Without it, the century is the race year's, moved back 100 years
    when that would place the birth after the race. Either way the horse must be between 1 and
    :data:`MAX_HORSE_AGE_YEARS` years old at the race.
    """

    n = no8.strip(" ")
    if not _BLOOD_RE.match(n):
        raise JrdbDecodeError("blood registration number is not 8 digits")
    if not 1900 <= race_year <= 2199:
        raise JrdbDecodeError("race year out of range")
    yy = int(n[:2])
    if birth_date is not None:
        if birth_date.year % 100 != yy:
            raise JrdbDecodeError("blood number year disagrees with the UKC birth date")
        year = birth_date.year
    else:
        year = race_year - race_year % 100 + yy
        if year > race_year:
            year -= 100
    age = race_year - year
    if not 1 <= age <= MAX_HORSE_AGE_YEARS:
        raise JrdbDecodeError("horse age implied by the blood number is implausible")
    return f"{year:04d}{n[2:]}"


def venue_code_jrdb_to_db(code: str | None) -> str | None:
    """JRDB 場コード → DB ``venue_code``. Identity for JRA 01〜10; None for 地方/海外 (21〜79)."""

    if code is None:
        return None
    c = code.strip(" ")
    if not c.isdigit():
        return None
    c = c.zfill(2)
    return c if c in JRA_VENUE_CODES else None


__all__ = [
    "MAX_HORSE_AGE_YEARS",
    "horse_id_from_blood_no",
    "key_from_text",
    "race_id_from_key",
    "resolve_year",
    "venue_code_jrdb_to_db",
]
