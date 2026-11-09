"""Column normalisation for the parity report (Feature 178, T034; contracts/parity.md).

Pure functions that bring a JRDB code and the DB's spelling of the same fact to one token, so
"同じ意味・表記が違う" columns (≈ in contracts/files-and-ids.md §4) can be compared. Nothing here
writes to the DB; the writer (US2) reuses the JRDB→DB direction only.

Code tables are transcribed from the public ``jrdb_code.txt`` (2019.05.26) and the DB spellings
from the three routes already in the repository (JRA-VAN ingest ``芝/ダ``・``1勝``, netkeiba
``１勝``・``ダ``, JRA official ``ダート``・``2勝クラス``).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

_WS_RE = re.compile(r"\s+")

# --- names --------------------------------------------------------------------------------


def norm_name(value: str | None, *, strip_shi: bool = False) -> str | None:
    """NFKC, remove every blank (half/full width); optionally drop a trailing 氏 (馬主名)."""

    if value is None:
        return None
    s = unicodedata.normalize("NFKC", value)
    s = _WS_RE.sub("", s)
    if strip_shi and s.endswith("氏"):
        s = s[:-1]
    return s or None


# --- track / going / weather --------------------------------------------------------------

TRACK_FROM_CODE = {1: "芝", 2: "ダ", 3: "障"}
_TRACK_DB = {"芝": "芝", "ダ": "ダ", "ダート": "ダ", "障": "障", "障害": "障"}


def track_from_code(code: int | None) -> str | None:
    return TRACK_FROM_CODE.get(code) if code is not None else None


def track_norm(value: str | None) -> str | None:
    if value is None:
        return None
    v = unicodedata.normalize("NFKC", value).strip()
    return _TRACK_DB.get(v, v or None)


def going_from_code(code: int | None) -> str | None:
    """10〜12 → 良, 20〜22 → 稍重, 30〜32 → 重, 40〜42 → 不良 (速/遅 variants collapse)."""

    if code is None:
        return None
    return {1: "良", 2: "稍重", 3: "重", 4: "不良"}.get(code // 10)


def going_norm(value: str | None) -> str | None:
    if value is None:
        return None
    v = unicodedata.normalize("NFKC", value).strip()
    return {"良": "良", "稍重": "稍重", "稍": "稍重", "重": "重", "不良": "不良", "不": "不良"}.get(
        v, v or None
    )


WEATHER_FROM_CODE = {1: "晴", 2: "曇", 3: "小雨", 4: "雨", 5: "小雪", 6: "雪"}


def weather_from_code(code: int | None) -> str | None:
    return WEATHER_FROM_CODE.get(code) if code is not None else None


def weather_norm(value: str | None) -> str | None:
    if value is None:
        return None
    return unicodedata.normalize("NFKC", value).strip() or None


# --- race class / grade / sex ---------------------------------------------------------------

#: 条件コード → class token (jrdb_code.txt 条件・条件グループコード)
CLASS_FROM_JOKEN = {
    "A1": "newcomer",
    "A2": "unraced",
    "A3": "maiden",
    "04": "win1",
    "05": "win1",
    "08": "win2",
    "09": "win2",
    "10": "win2",
    "15": "win3",
    "16": "win3",
    "OP": "open",
}
_CLASS_DB_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"新馬"), "newcomer"),
    (re.compile(r"未出走"), "unraced"),
    (re.compile(r"未勝利"), "maiden"),
    (re.compile(r"(^|[^\d])(1勝|400万|500万)"), "win1"),
    (re.compile(r"(^|[^\d])(2勝|800万|900万|1000万)"), "win2"),
    (re.compile(r"(^|[^\d])(3勝|1500万|1600万)"), "win3"),
    (re.compile(r"オープン|OP|ｵｰﾌﾟﾝ|G[1-3I]|GI|重賞|L\b|リステッド"), "open"),
)


def class_from_joken(code: str | None) -> str | None:
    if code is None:
        return None
    return CLASS_FROM_JOKEN.get(code.strip().upper())


def class_norm(value: str | None) -> str | None:
    """DB ``race_class`` spelling (any route) → class token; unknown spellings stay as text."""

    if value is None:
        return None
    v = unicodedata.normalize("NFKC", value).replace(" ", "")
    for pat, token in _CLASS_DB_RULES:
        if pat.search(v):
            return token
    return v or None


GRADE_FROM_CODE = {1: "G1", 2: "G2", 3: "G3", 4: "重賞", 5: "特別", 6: "L"}
_ROMAN = {"GIII": "G3", "GII": "G2", "GI": "G1"}


def grade_from_code(code: int | None) -> str | None:
    return GRADE_FROM_CODE.get(code) if code is not None else None


def grade_norm(value: str | None) -> str | None:
    if value is None:
        return None
    v = unicodedata.normalize("NFKC", value).replace(" ", "").upper()
    for roman, arabic in _ROMAN.items():
        if v.startswith(roman):
            return arabic
    return v or None


SEX_FROM_CODE = {1: "牡", 2: "牝", 3: "セ"}


def sex_from_code(code: int | None) -> str | None:
    return SEX_FROM_CODE.get(code) if code is not None else None


def sex_norm(value: str | None) -> str | None:
    if value is None:
        return None
    v = unicodedata.normalize("NFKC", value).strip()
    return {"セン": "セ", "騸": "セ"}.get(v, v or None)


# --- numbers ---------------------------------------------------------------------------------


def seconds(value) -> Decimal | None:
    """timedelta / Decimal / float / None → Decimal seconds (0.1 s resolution)."""

    if value is None:
        return None
    if hasattr(value, "total_seconds"):
        return Decimal(str(round(value.total_seconds(), 1)))
    return Decimal(str(value)).quantize(Decimal("0.1"))


__all__ = [
    "CLASS_FROM_JOKEN",
    "GRADE_FROM_CODE",
    "SEX_FROM_CODE",
    "TRACK_FROM_CODE",
    "WEATHER_FROM_CODE",
    "class_from_joken",
    "class_norm",
    "going_from_code",
    "going_norm",
    "grade_from_code",
    "grade_norm",
    "norm_name",
    "seconds",
    "sex_from_code",
    "sex_norm",
    "track_from_code",
    "track_norm",
    "weather_from_code",
    "weather_norm",
]
