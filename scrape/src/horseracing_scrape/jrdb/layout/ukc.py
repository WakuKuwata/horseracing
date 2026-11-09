"""UKC 馬基本データ — 第３版 (2002.08.20). Transcribed from the public spec
``https://jrdb.com/program/Ukc/ukc_doc.txt`` (fetched 2026-11-08). Record length 292.

父系統/母父系統コード are ``canonical_only``: JRDB's 186-line taxonomy is a different system
from the
DB's ``sire_line``/``damsire_line`` (contracts §4 ✗), so they only reach the JRDB canonical DB.
"""

from __future__ import annotations

from . import Field, Layout

LAYOUT = Layout(
    kind="UKC",
    version="3",
    record_length=292,
    doc="https://jrdb.com/program/Ukc/ukc_doc.txt 第３版 2002.08.20 (fetched 2026-11-08)",
    fields=(
        Field("blood_reg_no", 1, 8, "X", codec="code"),
        Field("horse_name", 9, 36, "X"),
        Field("sex_code", 45, 1, "9", "9"),
        Field("color_code", 46, 2, "9", target="skip"),
        Field("uma_kigo_code", 48, 2, "9", target="skip"),
        Field("sire_name", 50, 36, "X"),
        Field("dam_name", 86, 36, "X"),
        Field("damsire_name", 122, 36, "X"),
        Field("birth_date", 158, 8, "9", "9", codec="yyyymmdd"),
        Field("sire_birth_year", 166, 4, "9", target="skip"),
        Field("dam_birth_year", 170, 4, "9", target="skip"),
        Field("damsire_birth_year", 174, 4, "9", target="skip"),
        Field("owner_name", 178, 40, "X"),
        Field("owner_assoc_code", 218, 2, "9", target="skip"),
        Field("breeder_name", 220, 40, "X"),
        Field("birthplace", 260, 8, "X", target="skip"),  # 産地名(列が無い)
        Field("retired", 268, 1, "9", "9", codec="flag"),  # 1 抹消
        Field("data_date", 269, 8, "9", "9", codec="yyyymmdd"),
        Field("sire_line_code", 277, 4, "X", "9999", codec="code", target="canonical_only"),
        Field("damsire_line_code", 281, 4, "X", "9999", codec="code", target="canonical_only"),
        Field("reserved_285", 285, 6, "X", target="skip"),
    ),
)
