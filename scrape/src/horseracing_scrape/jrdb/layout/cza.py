"""CZA / CSA 調教師データ — 第１版 (2001.05.16). Transcribed from the public spec
``https://jrdb.com/program/Cs/Cs_doc1.txt`` (fetched 2026-11-08). Record length 272.
CZA = 全調教師, CSA = 今週出走分 (same layout).
"""

from __future__ import annotations

from . import Field, Layout

LAYOUT = Layout(
    kind="CZA",
    version="1",
    record_length=272,
    aliases=("CSA",),
    doc="https://jrdb.com/program/Cs/Cs_doc1.txt 第１版 2001.05.16 (fetched 2026-11-08)",
    fields=(
        Field("code", 1, 5, "X", "99999", codec="code"),
        Field("retired", 6, 1, "9", "9", codec="flag"),
        Field("retired_date", 7, 8, "9", "9", codec="yyyymmdd"),
        Field("name", 15, 12, "X"),
        Field("kana", 27, 30, "X"),
        Field("short_name", 57, 6, "X"),
        Field("affiliation_code", 63, 1, "9", "9"),
        Field("region_name", 64, 4, "X"),
        Field("birth_date", 68, 8, "9", "9", codec="yyyymmdd"),
        Field("first_license_year", 76, 4, "9", "9", codec="yyyy"),
        Field("comment", 80, 40, "X", target="skip"),  # ＪＲＤＢスタッフの厩舎見解
        Field("comment_date", 120, 8, "X", target="skip"),
        Field("this_year_stats", 128, 33, "Z", target="skip"),
        Field("last_year_stats", 161, 33, "Z", target="skip"),
        Field("career_stats", 194, 40, "Z", target="skip"),
        Field("data_date", 234, 8, "9", "9", codec="yyyymmdd"),
        Field("reserved_242", 242, 29, "X", target="skip"),
    ),
)
