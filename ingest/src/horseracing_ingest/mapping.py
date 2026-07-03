"""Map a parsed JRA-VAN row to core-table records (research R2/R4, data-model)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.validation import is_valid_race_id

from . import layout
from .parser import ParsedRow


class MappingError(ValueError):
    """Raised when a row cannot be mapped (unknown venue/status, bad race_id)."""


@dataclass
class StatusDecision:
    entry_status: str
    make_result_row: bool
    result_status: str | None
    finish_order: int | None


@dataclass
class CoreRecords:
    race_id: str
    race: dict
    horse: dict
    jockey: dict | None
    trainer: dict | None
    race_horse: dict
    race_result: dict | None  # None for non-starters (DNS, INV-1)


# --- small field helpers --------------------------------------------------

def _clean(value: str) -> str:
    # full-width and ascii whitespace -> stripped; full-width spaces (　) too.
    return value.replace("　", " ").strip()


def _none_if_empty(value: str) -> str | None:
    cleaned = _clean(value)
    return cleaned or None


def _to_int(value: str) -> int | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _to_meeting_int(value: str) -> int | None:
    """Meeting-position fields (kai / nichime / race) use a single-char extension
    where values >= 10 are encoded as A..F (A=10 .. F=15). E.g. day 10 -> "A"."""
    cleaned = _clean(value).upper()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    if len(cleaned) == 1 and "A" <= cleaned <= "F":
        return 10 + (ord(cleaned) - ord("A"))
    return None


def _to_decimal(value: str) -> Decimal | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_race_date(value: str) -> datetime.date:
    # "2007.8.11" -> date(2007, 8, 11)
    parts = _clean(value).split(".")
    if len(parts) != 3:
        raise MappingError(f"bad race_date: {value!r}")
    try:
        return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise MappingError(f"bad race_date: {value!r} ({exc})") from exc


def _parse_finish_time(value: str) -> datetime.timedelta | None:
    # "1.29.9" -> 1 min 29.9 sec; "0.00.0"/empty -> None
    cleaned = _clean(value)
    if not cleaned or cleaned in ("0.00.0", "0.0.0"):
        return None
    parts = cleaned.split(".")
    try:
        if len(parts) == 3:
            minutes, seconds, tenths = (int(p) for p in parts)
            return datetime.timedelta(minutes=minutes, seconds=seconds, milliseconds=tenths * 100)
    except ValueError:
        return None
    return None


def _parse_time_diff(value: str) -> datetime.timedelta | None:
    cleaned = _clean(value)
    if not cleaned or cleaned == "----":
        return None
    try:
        return datetime.timedelta(seconds=float(cleaned))
    except ValueError:
        return None


# --- id / venue / status --------------------------------------------------

def venue_to_code(name: str) -> str:
    cleaned = _clean(name)
    try:
        return layout.VENUE_CODE[cleaned]
    except KeyError as exc:
        raise MappingError(f"unknown venue: {name!r}") from exc


def derive_race_id(row: ParsedRow) -> str:
    date = _parse_race_date(row.fields[layout.RACE_DATE])
    venue = venue_to_code(row.fields[layout.VENUE_NAME])
    kai = _to_meeting_int(row.fields[layout.KAI])
    nichime = _to_meeting_int(row.fields[layout.NICHIME])
    race_no = _to_meeting_int(row.fields[layout.RACE_NUMBER])
    if None in (kai, nichime, race_no):
        raise MappingError("missing kai/nichime/race_number for race_id")
    race_id = f"{date.year:04d}{venue}{kai:02d}{nichime:02d}{race_no:02d}"
    if not is_valid_race_id(race_id):
        raise MappingError(f"derived race_id is invalid: {race_id!r}")
    return race_id


def _has_run_data(row: ParsedRow) -> bool:
    for idx in layout.CORNER_COLUMNS:
        v = _clean(row.fields[idx])
        if v and v != "0":
            return True
    return _parse_finish_time(row.fields[layout.FINISH_TIME]) is not None


def normalize_status(row: ParsedRow) -> StatusDecision:
    """research R4: finished / DNF / DNS, unknown -> error (never silent finished)."""
    raw = _clean(row.fields[layout.FINISH_ORDER])
    if raw.isdigit():
        order = int(raw)
    else:
        raise MappingError(f"unparseable finish_order: {raw!r}")

    if order >= 1:
        return StatusDecision(EntryStatus.STARTED, True, ResultStatus.FINISHED, order)

    # order == 0 -> non-finisher
    if _has_run_data(row):
        # DNF: ran but did not finish (default stopped; disqualified refinement is
        # best-effort once the abnormal-division column is identified).
        return StatusDecision(EntryStatus.STARTED, True, ResultStatus.STOPPED, None)
    # DNS: never ran -> no race_results row (INV-1). cancelled default; excluded best-effort.
    return StatusDecision(EntryStatus.CANCELLED, False, None, None)


def _corner_orders(row: ParsedRow) -> list[str] | None:
    orders = [
        _clean(row.fields[idx])
        for idx in layout.CORNER_COLUMNS
        if _clean(row.fields[idx]) and _clean(row.fields[idx]) != "0"
    ]
    return orders or None


def _birth_year(value: str) -> int | None:
    cleaned = _clean(value)
    if len(cleaned) >= 4 and cleaned[:4].isdigit():
        return int(cleaned[:4])
    return None


# --- top-level mapping ----------------------------------------------------

def to_core_records(row: ParsedRow) -> CoreRecords:
    race_id = derive_race_id(row)
    status = normalize_status(row)
    horse_id = _clean(row.fields[layout.BLOOD_REG_NO])
    if not horse_id:
        raise MappingError("missing horse_id (blood registration number)")

    race = {
        "race_id": race_id,
        "race_date": _parse_race_date(row.fields[layout.RACE_DATE]),
        "venue_code": venue_to_code(row.fields[layout.VENUE_NAME]),
        "race_number": _to_int(row.fields[layout.RACE_NUMBER]),
        "race_name": _none_if_empty(row.fields[layout.RACE_NAME_FULL])
        or _none_if_empty(row.fields[layout.RACE_NAME_SHORT]),
        "race_name_short": _none_if_empty(row.fields[layout.RACE_NAME_SHORT]),
        "race_class": _none_if_empty(row.fields[layout.RACE_CLASS]),
        "grade": _none_if_empty(row.fields[layout.GRADE]),
        "track_type": _none_if_empty(row.fields[layout.TRACK_TYPE]),
        "distance": _to_int(row.fields[layout.DISTANCE]),
        "going": _none_if_empty(row.fields[layout.GOING]),
        "weather": _none_if_empty(row.fields[layout.WEATHER]),
        # Feature 055: 1着賞金 (万円) — race-constant pre-published condition; 0/empty -> NULL
        "prize_money": _to_int(row.fields[layout.PRIZE_MONEY]) or None,
    }

    horse = {
        "horse_id": horse_id,
        "horse_name": _none_if_empty(row.fields[layout.HORSE_NAME]),
        "sex": _none_if_empty(row.fields[layout.SEX]),
        "birth_year": _birth_year(row.fields[layout.BIRTH_DATE]),
        "sire_name": _none_if_empty(row.fields[layout.SIRE_NAME]),
        "dam_name": _none_if_empty(row.fields[layout.DAM_NAME]),
        "damsire_name": _none_if_empty(row.fields[layout.DAMSIRE_NAME]),
        "data_source": "jra_van",
        # Feature 055: owner is last-write-wins (transfers rare, 026 sire_name precedent)
        "owner_name": _none_if_empty(row.fields[layout.OWNER_NAME]),
        "breeder_name": _none_if_empty(row.fields[layout.BREEDER_NAME]),
        "sire_line": _none_if_empty(row.fields[layout.SIRE_LINE]),
        "damsire_line": _none_if_empty(row.fields[layout.DAMSIRE_LINE]),
    }

    jockey_id = _none_if_empty(row.fields[layout.JOCKEY_CODE])
    jockey = (
        {"jockey_id": jockey_id, "jockey_name": _none_if_empty(row.fields[layout.JOCKEY_NAME])}
        if jockey_id
        else None
    )
    trainer_id = _none_if_empty(row.fields[layout.TRAINER_CODE])
    trainer = (
        {"trainer_id": trainer_id, "trainer_name": _none_if_empty(row.fields[layout.TRAINER_NAME])}
        if trainer_id
        else None
    )

    race_horse = {
        "race_id": race_id,
        "horse_id": horse_id,
        "sex": _none_if_empty(row.fields[layout.SEX]),
        "age": _to_int(row.fields[layout.AGE]),
        "frame": _to_int(row.fields[layout.FRAME]),
        "horse_number": _to_int(row.fields[layout.HORSE_NUMBER]),
        "jockey_id": jockey_id,
        "trainer_id": trainer_id,
        "weight": _to_int(row.fields[layout.HORSE_WEIGHT]),
        "weight_diff": _to_int(row.fields[layout.WEIGHT_DIFF]),
        "odds": _to_decimal(row.fields[layout.ODDS]),  # result-time (provenance: R5)
        "popularity": _to_int(row.fields[layout.POPULARITY]),
        "running_style": _none_if_empty(row.fields[layout.RUNNING_STYLE]),
        "jockey_weight": _to_decimal(row.fields[layout.JOCKEY_WEIGHT]),
        "entry_status": status.entry_status,
    }

    race_result = None
    if status.make_result_row:
        race_result = {
            "race_id": race_id,
            "horse_id": horse_id,
            "finish_order": status.finish_order,
            "finish_time": _parse_finish_time(row.fields[layout.FINISH_TIME]),
            "finish_time_diff": _parse_time_diff(row.fields[layout.TIME_DIFF]),
            "corner_orders": _corner_orders(row),
            "last_3f": _to_decimal(row.fields[layout.LAST_3F]),
            # Feature 055: テン3F — result-derived, as-of features only (II)
            "first_3f": _to_decimal(row.fields[layout.FIRST_3F]),
            "result_status": status.result_status,
        }

    return CoreRecords(race_id, race, horse, jockey, trainer, race_horse, race_result)
