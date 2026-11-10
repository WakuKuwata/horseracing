"""Shift_JIS fixed-length records → value objects (Feature 178, T008). Pure functions.

Framing
-------
The doc's レコード長 counts a 2-byte terminator (ＣＲ・ＬＦ). Member deliveries are CRLF; the public
samples under ``/program/`` were observed with the CR dropped (``LF`` only: HJC, TYB) or doubled
(``LF LF``: UKC, KSA). The accepted terminators are therefore exactly ``CRLF``, ``LF`` and
``LFLF``; every physical line is classified, and 桁ずれ is judged on the DATA part
(``record_length - 2`` bytes). cp932 never places 0x0A/0x0D inside a double-byte character, so
splitting on LF is safe, and a CR *inside* the data part is a framing error, not data.

A row that is too short/long, carries a stray CR, or is not terminated is *rejected* (counted
with its physical line number, record number and a reason — never its bytes, FR-022) and nothing
is built from it (FR-021a fail-closed). A file whose every row is rejected raises
:class:`JrdbLayoutMismatch` (wrong version/layout). A file with rows AND rejections is a
*partial* decode: callers must record ``rejected_rows`` (``rows_rejected`` in the job summary,
``race_status`` in ``jrdb_files``) — the decoder cannot do that for them (codex D20 #6).

Values
------
Every numeric field is validated against the doc's picture (``fmt``): integer pictures accept an
optional leading ``-`` and ASCII digits only; decimal pictures require the literal decimal point
and digits; blank = ``None``; nothing else (no ``1.``, no exponent, no NaN — codex D20 #1).
Exception messages carry positions and field names, never the bytes (codex D20 #7).

Profiles (research D7a)
-----------------------
``profile="current"`` leaves every ``canonical_only`` field out of the value object (``None``) and
the result is checked once more before it is returned; ``profile="jrdb_canonical"`` decodes them.
``skip`` fields are never read under either profile.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .errors import JrdbDecodeError, JrdbLayoutMismatch
from .layout import Field, Layout, layout_for
from .models import (
    JrdbEntry,
    JrdbFileMeta,
    JrdbHorse,
    JrdbPayouts,
    JrdbPerson,
    JrdbPreRace,
    JrdbRaceKey,
    JrdbRaceProgram,
    JrdbRaceResult,
    JrdbResult,
)
from .profile import CANONICAL_ONLY_ATTRIBUTES, includes_canonical, profile_from_arg

ENCODING = "cp932"

_INT_RE = re.compile(r"^-?\d+$")
_DEC_RE = re.compile(r"^-?\d*\.\d+$")
_DIGITS_RE = re.compile(r"^\d+$")
_SIGNED_RE = re.compile(r"^([+-]?)([ \d]*\d[ \d]*)$")  # XZ9: sign, then digits with inner blanks

RejectReason = str  # "length" | "cr_in_data" | "unterminated"


@dataclass(frozen=True)
class RejectedRow:
    line_no: int  # physical 1-based line number in the file
    record_no: int  # 1-based position among non-blank lines
    actual_length: int  # data bytes found (no content is kept)
    reason: RejectReason = "length"


@dataclass(frozen=True)
class Framing:
    terminator: str  # "CRLF" | "LF" | "LFLF" | "mixed" | "none"
    counts: dict[str, int]  # per terminator kind
    extra_blank_lines: int  # blank lines beyond the one LFLF allows


@dataclass(frozen=True)
class DecodedFile:
    kind: str
    version: str
    profile: str
    rows: tuple[Any, ...]
    rejected_rows: tuple[RejectedRow, ...]
    record_length: int  # the layout's (doc) record length
    framing: Framing

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def line_terminator(self) -> str:
        return self.framing.terminator

    @property
    def partial(self) -> bool:
        return bool(self.rows) and bool(self.rejected_rows)


# --------------------------------------------------------------------------- framing


def split_records(
    raw: bytes, data_length: int
) -> tuple[list[tuple[int, int, bytes]], list[RejectedRow], Framing]:
    """Return ``(records, rejected, framing)``; records are ``(line_no, record_no, data)``.

    Physical lines are split on LF. A line ending in CR is CRLF-terminated; a non-blank line
    followed by exactly one blank line is LFLF-terminated; otherwise LF. The last line must be
    terminated (a trailing LF), else it is rejected as ``unterminated``. A CR anywhere in the
    data part rejects the record (``cr_in_data``). Blank lines beyond the one LFLF allows are
    counted in ``extra_blank_lines`` (they are not records and carry no data).
    """

    records: list[tuple[int, int, bytes]] = []
    rejected: list[RejectedRow] = []
    counts = {"CRLF": 0, "LF": 0, "LFLF": 0}
    extra_blank = 0
    terminated_tail = raw.endswith(b"\n")
    lines = raw.split(b"\n")
    if terminated_tail:
        lines.pop()  # the final LF terminates the last line; nothing follows it
    record_no = 0
    blank_run = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        if line == b"":
            blank_run += 1
            if blank_run > 1:
                extra_blank += 1
            i += 1
            continue
        blank_run = 0
        record_no += 1
        crlf = line.endswith(b"\r")
        data = line[:-1] if crlf else line
        is_last = i == len(lines) - 1
        if is_last and not terminated_tail:
            rejected.append(RejectedRow(line_no, record_no, len(data), "unterminated"))
            i += 1
            continue
        if b"\r" in data:
            rejected.append(RejectedRow(line_no, record_no, len(data), "cr_in_data"))
            i += 1
            continue
        if crlf:
            counts["CRLF"] += 1
        elif i + 1 < len(lines) and lines[i + 1] == b"":
            counts["LFLF"] += 1
            blank_run = 1
            i += 1  # consume the blank that belongs to this record
        else:
            counts["LF"] += 1
        if len(data) != data_length:
            rejected.append(RejectedRow(line_no, record_no, len(data), "length"))
        else:
            records.append((line_no, record_no, data))
        i += 1
    kinds = [k for k, v in counts.items() if v]
    if not kinds:
        term = "none"
    elif len(kinds) == 1:
        term = kinds[0]
    else:
        term = "mixed"
    return records, rejected, Framing(term, counts, extra_blank)


# --------------------------------------------------------------------------- field decoding


def _text(data: bytes, f: Field, line_no: int) -> str:
    try:
        return data[f.slice].decode(ENCODING)
    except UnicodeDecodeError:
        raise JrdbDecodeError("cp932 decode failed", line_no=line_no, field=f.name) from None


def _blank(text: str) -> bool:
    return text.strip(" 　") == ""


def _numeric(text: str, f: Field, line_no: int) -> int | Decimal | None:
    if _blank(text):
        return None
    t = text.strip(" ")
    if "." in f.fmt:
        if not _DEC_RE.match(t):
            raise JrdbDecodeError(
                "not a decimal of the documented picture", line_no=line_no, field=f.name
            )
        return Decimal(t)
    if not _INT_RE.match(t):
        raise JrdbDecodeError(
            "not an integer of the documented picture", line_no=line_no, field=f.name
        )
    value = int(t)
    if f.scale != 1:
        return Decimal(value) / Decimal(f.scale)
    return value


def decode_field(data: bytes, f: Field, line_no: int) -> Any:
    text = _text(data, f, line_no)
    codec = f.codec
    if codec == "code":
        t = text.strip(" 　")
        return t or None
    if codec == "flag":  # 1 = set; 0 / blank = not set; anything else is not a flag
        t = text.strip(" ")
        if t == "1":
            return True
        if t in ("", "0"):
            return False
        raise JrdbDecodeError("not a 0/1 flag", line_no=line_no, field=f.name)
    if codec == "signed":  # XZ9: ``- 4`` → -4; all-blank → None; sign without digits → error
        if _blank(text):
            return None
        m = _SIGNED_RE.match(text)
        if not m:
            raise JrdbDecodeError("not a signed number (XZ9)", line_no=line_no, field=f.name)
        digits = m.group(2).replace(" ", "")
        return -int(digits) if m.group(1) == "-" else int(digits)
    if codec == "yyyymmdd":
        t = text.strip(" ")
        if t == "" or t == "0" * len(t):
            return None
        if len(t) != 8 or not _DIGITS_RE.match(t):
            raise JrdbDecodeError("not YYYYMMDD", line_no=line_no, field=f.name)
        try:
            return datetime.date(int(t[0:4]), int(t[4:6]), int(t[6:8]))
        except ValueError:
            raise JrdbDecodeError("not a calendar date", line_no=line_no, field=f.name) from None
    if codec == "yyyy":
        t = text.strip(" ")
        if t == "" or t == "0" * len(t):
            return None
        if len(t) != 4 or not _DIGITS_RE.match(t):
            raise JrdbDecodeError("not YYYY", line_no=line_no, field=f.name)
        return int(t)
    if codec == "hhmm":
        t = text.strip(" ")
        if t == "" or t == "0" * len(t):
            return None
        if len(t) != 4 or not _DIGITS_RE.match(t):
            raise JrdbDecodeError("not HHMM", line_no=line_no, field=f.name)
        hh, mm = int(t[:2]), int(t[2:])
        if hh > 23 or mm > 59:
            raise JrdbDecodeError("HHMM out of range", line_no=line_no, field=f.name)
        return datetime.time(hh, mm)
    if codec == "time_m_sss":  # 1 digit 分 + 3 digits 0.1 秒; 0000/blank = no time
        t = text.strip(" ")
        if t == "" or t == "0000":
            return None
        if len(t) != 4 or not _DIGITS_RE.match(t):
            raise JrdbDecodeError("not a 9999 time", line_no=line_no, field=f.name)
        minutes, tenths = int(t[0]), int(t[1:])
        if tenths >= 600:
            raise JrdbDecodeError("time seconds out of range", line_no=line_no, field=f.name)
        return datetime.timedelta(seconds=minutes * 60, microseconds=tenths * 100_000)
    if f.type in ("9", "Z"):
        return _numeric(text, f, line_no)
    if f.type == "F":
        t = text.strip(" ")
        return t or None
    # X
    t = text.strip(" 　")
    return t or None


def _decode_record(
    data: bytes, layout: Layout, *, include_canonical: bool, line_no: int
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in layout.adopted(include_canonical=include_canonical):
        out[f.name] = decode_field(data, f, line_no)
    return out


# --------------------------------------------------------------------------- builders


def _require(d: dict[str, Any], name: str, line_no: int) -> Any:
    v = d.get(name)
    if v is None:
        raise JrdbDecodeError("required field is blank", line_no=line_no, field=name)
    return v


def _body_weight(d: dict[str, Any]) -> tuple[int | None, int | None]:
    """JRDB writes ``000`` for 馬体重 when there is no measurement (取消・除外・未計量). A 0 kg
    horse does not exist, so 0 → None, and a 増減 without a weight is dropped with it (the pair
    rule of the body_weight group; R5 2026-11-09 found 4 取消馬 written as 0)."""

    w = d.get("weight")
    if w is None or int(w) == 0:
        return None, None
    return w, d.get("weight_diff")


def _race_key(d: dict[str, Any], line_no: int) -> JrdbRaceKey:
    try:
        return JrdbRaceKey(
            venue=f"{int(_require(d, 'venue', line_no)):02d}",
            yy=int(_require(d, "yy", line_no)),
            kai=int(_require(d, "kai", line_no)),
            day_hex=str(_require(d, "day_hex", line_no)).lower(),
            r=int(_require(d, "r", line_no)),
        )
    except ValueError:
        raise JrdbDecodeError("race key out of range", line_no=line_no, field="race_key") from None


def _code5(v: Any, line_no: int, field: str) -> str | None:
    if v is None:
        return None
    s = str(v).strip(" ")
    if s == "" or s == "0" * len(s):
        return None
    if not _DIGITS_RE.match(s) or len(s) > 5:
        raise JrdbDecodeError("not a 5-digit code", line_no=line_no, field=field)
    return s.zfill(5)


def _build_bac(d, line_no) -> JrdbRaceProgram:
    return JrdbRaceProgram(
        key=_race_key(d, line_no),
        race_date=_require(d, "race_date", line_no),
        post_time=d.get("post_time"),
        distance=d.get("distance"),
        track_code=d.get("track_code"),
        turn_code=d.get("turn_code"),
        inout_code=d.get("inout_code"),
        shubetsu_code=d.get("shubetsu_code"),
        joken_code=d.get("joken_code"),
        kigo_code=d.get("kigo_code"),
        juryo_code=d.get("juryo_code"),
        grade_code=d.get("grade_code"),
        race_name=d.get("race_name"),
        head_count=d.get("head_count"),
        race_name_short=d.get("race_name_short"),
        race_name_9=d.get("race_name_9"),
        data_kubun=d.get("data_kubun"),
        prizes_man=tuple(d.get(f"prize_{i}_man") for i in range(1, 6)),  # type: ignore[arg-type]
        sale_flags=d.get("sale_flags"),
    )


def _build_kyi(d, line_no) -> JrdbEntry:
    return JrdbEntry(
        key=_race_key(d, line_no),
        horse_number=int(_require(d, "horse_number", line_no)),
        blood_reg_no=str(_require(d, "blood_reg_no", line_no)),
        horse_name=d.get("horse_name"),
        jockey_name=d.get("jockey_name"),
        carried_weight=d.get("carried_weight"),
        apprentice_code=d.get("apprentice_code"),
        trainer_name=d.get("trainer_name"),
        trainer_affiliation=d.get("trainer_affiliation"),
        frame=d.get("frame"),
        jockey_code=_code5(d.get("jockey_code"), line_no, "jockey_code"),
        trainer_code=_code5(d.get("trainer_code"), line_no, "trainer_code"),
        weight=_body_weight(d)[0],
        weight_diff=_body_weight(d)[1],
        cancelled=bool(d.get("cancelled")),
        sex_code=d.get("sex_code"),
        owner_name=d.get("owner_name"),
    )


def _build_ukc(d, line_no, *, include_canonical: bool) -> JrdbHorse:
    return JrdbHorse(
        blood_reg_no=str(_require(d, "blood_reg_no", line_no)),
        horse_name=d.get("horse_name"),
        sex_code=d.get("sex_code"),
        sire_name=d.get("sire_name"),
        dam_name=d.get("dam_name"),
        damsire_name=d.get("damsire_name"),
        birth_date=d.get("birth_date"),
        owner_name=d.get("owner_name"),
        breeder_name=d.get("breeder_name"),
        retired=bool(d.get("retired")),
        data_date=d.get("data_date"),
        sire_line_code=d.get("sire_line_code") if include_canonical else None,
        damsire_line_code=d.get("damsire_line_code") if include_canonical else None,
        line_codes=(d.get("sire_line_code"), d.get("damsire_line_code")),
    )


def _build_person(d, line_no, *, kind: str) -> JrdbPerson:
    code = _code5(_require(d, "code", line_no), line_no, "code")
    if code is None:
        raise JrdbDecodeError("person code is zero", line_no=line_no, field="code")
    return JrdbPerson(
        kind=kind,
        code=code,
        retired=bool(d.get("retired")),
        retired_date=d.get("retired_date"),
        name=d.get("name"),
        kana=d.get("kana"),
        short_name=d.get("short_name"),
        affiliation_code=d.get("affiliation_code"),
        region_name=d.get("region_name"),
        birth_date=d.get("birth_date"),
        first_license_year=d.get("first_license_year"),
        data_date=d.get("data_date"),
        apprentice_code=d.get("apprentice_code"),
        stable_trainer_code=_code5(d.get("stable_trainer_code"), line_no, "stable_trainer_code"),
    )


def _build_tyb(d, line_no) -> JrdbPreRace:
    return JrdbPreRace(
        key=_race_key(d, line_no),
        horse_number=int(_require(d, "horse_number", line_no)),
        cancelled=bool(d.get("cancelled")),
        jockey_code=_code5(d.get("jockey_code"), line_no, "jockey_code"),
        jockey_name=d.get("jockey_name"),
        carried_weight=d.get("carried_weight"),
        apprentice_code=d.get("apprentice_code"),
        going_code=d.get("going_code"),
        weather_code=d.get("weather_code"),
        weight=_body_weight(d)[0],
        weight_diff=_body_weight(d)[1],
        post_time=d.get("post_time"),
    )


def _build_sed(d, line_no, *, include_canonical: bool) -> JrdbResult:
    raw = tuple(d.get(f"corner_{i}") for i in range(1, 5))
    packed = tuple(None if c in (None, 0) else int(c) for c in raw)
    corners = packed if include_canonical else None
    return JrdbResult(
        key=_race_key(d, line_no),
        horse_number=int(_require(d, "horse_number", line_no)),
        blood_reg_no=str(_require(d, "blood_reg_no", line_no)),
        race_date=_require(d, "race_date", line_no),
        horse_name=d.get("horse_name"),
        distance=d.get("distance"),
        track_code=d.get("track_code"),
        turn_code=d.get("turn_code"),
        inout_code=d.get("inout_code"),
        going_code=d.get("going_code"),
        shubetsu_code=d.get("shubetsu_code"),
        joken_code=d.get("joken_code"),
        kigo_code=d.get("kigo_code"),
        juryo_code=d.get("juryo_code"),
        grade_code=d.get("grade_code"),
        race_name=d.get("race_name"),
        head_count=d.get("head_count"),
        race_name_short=d.get("race_name_short"),
        finish_order=d.get("finish_order"),
        abnormal_code=d.get("abnormal_code"),
        finish_time=d.get("finish_time"),
        jockey_weight=d.get("jockey_weight"),
        jockey_name=d.get("jockey_name"),
        trainer_name=d.get("trainer_name"),
        final_win_odds=d.get("final_win_odds"),
        final_win_popularity=d.get("final_win_popularity"),
        finish_time_diff=d.get("finish_time_diff"),
        last_3f=d.get("last_3f"),
        jockey_code=_code5(d.get("jockey_code"), line_no, "jockey_code"),
        trainer_code=_code5(d.get("trainer_code"), line_no, "trainer_code"),
        weight=_body_weight(d)[0],
        weight_diff=_body_weight(d)[1],
        weather_code=d.get("weather_code"),
        win_payout_yen=d.get("win_payout_yen"),
        place_payout_yen=d.get("place_payout_yen"),
        post_time=d.get("post_time"),
        first_3f=d.get("first_3f") if include_canonical else None,
        corner_orders=corners if include_canonical else None,
        running_style_code=d.get("running_style_code") if include_canonical else None,
        style_inputs=(
            d.get("running_style_code"),
            tuple(c for c in packed if c is not None),
        ),
    )


def _build_srb(d, line_no) -> JrdbRaceResult:
    laps: list[Decimal] = []
    for i in range(1, 19):
        v = d.get(f"lap_{i:02d}")
        if v is None or v == 0:
            break
        laps.append(Decimal(v))
    bias = {
        k: d.get(k)
        for k in (
            "bias_corner_1",
            "bias_corner_2",
            "bias_backstretch",
            "bias_corner_3",
            "bias_corner_4",
            "bias_straight",
        )
    }
    return JrdbRaceResult(key=_race_key(d, line_no), lap_times=tuple(laps), track_bias=bias)


def _combo(text: str | None, width: int, line_no: int, field: str) -> tuple[int, ...] | None:
    if text is None:
        return None
    t = text.strip(" ")
    if t == "" or t == "0" * len(t):
        return None
    if not _DIGITS_RE.match(t) or len(t) % width:
        raise JrdbDecodeError("not a horse-number combination", line_no=line_no, field=field)
    nums = tuple(int(t[i : i + width]) for i in range(0, len(t), width))
    if any(n == 0 for n in nums):
        return None
    return nums


def _build_hjc(d, line_no) -> JrdbPayouts:
    def singles(prefix: str, count: int):
        out = []
        for i in range(1, count + 1):
            name = f"{prefix}_{i}_number"
            num = _combo(d.get(name), 2, line_no, name)
            yen = d.get(f"{prefix}_{i}_yen")
            if num and yen:
                out.append((num[0], int(yen)))
        return tuple(out)

    def combos(prefix: str, count: int):
        out = []
        for i in range(1, count + 1):
            name = f"{prefix}_{i}_combo"
            combo = _combo(d.get(name), 2, line_no, name)
            yen = d.get(f"{prefix}_{i}_yen")
            if combo and yen:
                out.append((combo, int(yen)))
        return tuple(out)

    return JrdbPayouts(
        key=_race_key(d, line_no),
        win=singles("win", 3),
        place=singles("place", 5),
        quinella=combos("quinella", 3),
        wide=combos("wide", 7),
        exacta=combos("exacta", 6),
        trio=combos("trio", 3),
        trifecta=combos("trifecta", 6),
    )


def _build_row(kind: str, d: dict[str, Any], *, line_no: int, include_canonical: bool) -> Any:
    k = kind.upper()
    if k == "BAC":
        return _build_bac(d, line_no)
    if k == "KYI":
        return _build_kyi(d, line_no)
    if k == "UKC":
        return _build_ukc(d, line_no, include_canonical=include_canonical)
    if k in ("KZA", "KSA"):
        return _build_person(d, line_no, kind="jockey")
    if k in ("CZA", "CSA"):
        return _build_person(d, line_no, kind="trainer")
    if k == "TYB":
        return _build_tyb(d, line_no)
    if k == "SED":
        return _build_sed(d, line_no, include_canonical=include_canonical)
    if k == "SRB":
        return _build_srb(d, line_no)
    if k == "HJC":
        return _build_hjc(d, line_no)
    raise JrdbDecodeError("no builder for this kind", line_no=line_no)


def _assert_no_canonical(row: Any, line_no: int) -> None:
    """Final guard: under ``current`` no canonical-only attribute may carry a value."""

    if not dataclasses.is_dataclass(row):
        return
    for attr in CANONICAL_ONLY_ATTRIBUTES.get(type(row).__name__, ()):
        if getattr(row, attr, None) is not None:
            raise JrdbDecodeError(
                "canonical-only attribute decoded under profile=current",
                line_no=line_no,
                field=attr,
            )


# --------------------------------------------------------------------------- entry point


def decode_file(raw: bytes, meta: JrdbFileMeta, *, profile: str) -> DecodedFile:
    """Decode one file. ``profile`` is required (D7a): ``"current"`` or ``"jrdb_canonical"``."""

    prof = profile_from_arg(profile)
    include_canonical = includes_canonical(prof)
    layout = layout_for(meta.kind, meta.version)
    records, rejected, framing = split_records(raw, layout.data_length)
    if not records and rejected:
        first = rejected[0]
        raise JrdbLayoutMismatch(
            layout.kind, layout.record_length, first.actual_length + 2, line_no=first.line_no
        )
    rows = []
    for line_no, _record_no, data in records:
        # the canonical-only FIELDS are always decoded (2026-11-10): under ``current`` the builders
        # keep them only as translation inputs (style_inputs / line_codes), never as attributes
        d = _decode_record(data, layout, include_canonical=True, line_no=line_no)
        row = _build_row(meta.kind, d, line_no=line_no, include_canonical=include_canonical)
        if not include_canonical:
            _assert_no_canonical(row, line_no)
        rows.append(row)
    return DecodedFile(
        kind=meta.kind.upper(),
        version=layout.version,
        profile=prof.name,
        rows=tuple(rows),
        rejected_rows=tuple(rejected),
        record_length=layout.record_length,
        framing=framing,
    )


__all__ = [
    "DecodedFile",
    "Framing",
    "RejectedRow",
    "decode_field",
    "decode_file",
    "split_records",
]
