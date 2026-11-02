"""公式 ID の名前空間契約と名前照合キー(Feature 176, FR-009/010, data-model §6).

* 馬 = 血統登録番号 10 桁(リンク ``pw01dud00`` の直後)
* 騎手 = 5 桁(``pw04kmk0`` の直後)
* 調教師 = 5 桁(``pw05cmk0`` の直後)

いずれも JRA が発行する正規 ID と同一名前空間なので ``id_mappings`` を経由せずそのまま使う。
抽出は境界つき正規表現で、桁数・非数字・prefix 不一致は **None(未知 ID)** を返し、呼び出し側が
レース単位で隔離する。先頭ゼロは保持する(文字列)。騎手と調教師の同じ数値は別エンティティ。

名前照合キーは**異常検知専用**(同 ID 別名の報告)で、結合キーにも同一性の証明にも使わない。
"""

from __future__ import annotations

import re
import unicodedata

from ..identity import strip_markers

HORSE = "horse"
JOCKEY = "jockey"
TRAINER = "trainer"

#: kind -> pattern. The check value ``/XX`` after the id is NOT part of the id. The look-behind
#: rejects a longer token that merely ends with the prefix; the look-ahead rejects extra digits.
_PATTERNS: dict[str, re.Pattern[str]] = {
    HORSE: re.compile(r"(?<![0-9A-Za-z])pw01dud00(\d{10})(?![0-9])"),
    JOCKEY: re.compile(r"(?<![0-9A-Za-z])pw04kmk0(\d{5})(?![0-9])"),
    TRAINER: re.compile(r"(?<![0-9A-Za-z])pw05cmk0(\d{5})(?![0-9])"),
}
_WS = re.compile(r"\s+")


def official_id(kind: str, href: str | None) -> str | None:
    """Extract the official id of ``kind`` from a link href / onclick string, or None.

    Returns None (never raises on input) for a missing href, a different prefix, the wrong number
    of digits, or non-digit characters — the caller quarantines the race (FR-009)."""
    if href is None:
        return None
    pat = _PATTERNS.get(kind)
    if pat is None:
        raise ValueError(f"unknown id kind: {kind!r}")
    m = pat.search(href)
    return m.group(1) if m else None


def jra_name_key(name: str | None) -> str:
    """NFKC + remove ALL whitespace (half/full width) + strip 067 markers (異常検知専用)."""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = _WS.sub("", s)
    return strip_markers(s)
