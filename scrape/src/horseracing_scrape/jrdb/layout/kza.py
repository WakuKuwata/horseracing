"""KZA / KSA 騎手データ — 第１版ｃ (2023.10.20). Transcribed from the public spec
``https://jrdb.com/program/Ks/Ks_doc1.txt`` (fetched 2026-11-08). Record length 272.
KZA = 全騎手, KSA = 今週出走分 (same layout).

The adopted fields are the id-mapping material (research D3): JRDB code, name, kana, birth date,
affiliation, first licence year. Comments and leading/results tables are ``skip``.
"""

from __future__ import annotations

from . import Field, Layout

LAYOUT = Layout(
    kind="KZA",
    version="1",
    record_length=272,
    aliases=("KSA",),
    doc="https://jrdb.com/program/Ks/Ks_doc1.txt 第１版ｃ 2023.10.20 (fetched 2026-11-08)",
    fields=(
        Field("code", 1, 5, "X", "99999", codec="code"),
        Field("retired", 6, 1, "9", "9", codec="flag"),  # 1 抹消 0 現役
        Field("retired_date", 7, 8, "9", "9", codec="yyyymmdd"),
        Field("name", 15, 12, "X"),  # 全角 6
        Field("kana", 27, 30, "X"),  # 全角 15
        Field("short_name", 57, 6, "X"),  # 全角 3
        Field("affiliation_code", 63, 1, "9", "9"),  # 1 関東 2 関西 3 他
        Field("region_name", 64, 4, "X"),  # 地方の場合
        Field("birth_date", 68, 8, "9", "9", codec="yyyymmdd"),
        Field("first_license_year", 76, 4, "9", "9", codec="yyyy"),
        Field("apprentice_code", 80, 1, "9", "9"),
        Field("stable_trainer_code", 81, 5, "X", "99999", codec="code"),
        Field("comment", 86, 40, "X", target="skip"),  # ＪＲＤＢスタッフの騎手評価
        Field("comment_date", 126, 8, "X", target="skip"),
        Field(
            "this_year_stats", 134, 33, "Z", target="skip"
        ),  # リーディング・平地/障害成績・特別/重賞勝数
        Field("last_year_stats", 167, 33, "Z", target="skip"),
        Field("career_stats", 200, 40, "Z", target="skip"),  # 通算平地/障害 (5×4 ×2)
        Field("data_date", 240, 8, "X", "X", codec="yyyymmdd"),  # 仕様書上は X(スペース可)
        Field("reserved_248", 248, 23, "X", target="skip"),
    ),
)
