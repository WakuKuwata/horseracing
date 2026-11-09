"""TYB 直前情報データ — 第４b版 (2022.08.22). Transcribed from the public spec
``https://jrdb.com/program/Tyb/tyb_doc.txt`` (fetched 2026-11-08). Record length 128.

Two file kinds share it: 直前データ (``TYB_E/W/L.txt``, per-venue overwrite, "next race only") and
直前累積データ (``TYByymmdd.txt``). Adopted: 取消, 騎手コード/名, 負担重量, 見習い, 馬場/天候,
馬体重(増減),
発走時間. The 単勝/複勝オッズ snapshot and オッズ取得時間 are ``skip`` (contract §3: TYB odds are
never written; 憲法 V keeps a single latest odds value from the official route).
"""

from __future__ import annotations

from . import Field, Layout, race_key_fields

LAYOUT = Layout(
    kind="TYB",
    version="4",
    record_length=128,
    doc="https://jrdb.com/program/Tyb/tyb_doc.txt 第４b版 2022.08.22 (fetched 2026-11-08)",
    fields=(
        *race_key_fields(),
        Field("horse_number", 9, 2, "9", "99"),
        Field("idm", 11, 5, "Z", "ZZ9.9", target="skip"),
        Field("jockey_index", 16, 5, "Z", "ZZ9.9", target="skip"),
        Field("info_index", 21, 5, "Z", "ZZ9.9", target="skip"),
        Field("odds_index", 26, 5, "Z", "ZZ9.9", target="skip"),
        Field("paddock_index", 31, 5, "Z", "ZZ9.9", target="skip"),
        Field("reserved_index_1", 36, 5, "Z", "ZZ9.9", target="skip"),
        Field("sogo_index", 41, 5, "Z", "ZZ9.9", target="skip"),
        Field("gear_change", 46, 1, "9", target="skip"),
        Field("leg_condition", 47, 1, "9", target="skip"),
        Field("cancelled", 48, 1, "9", "9", codec="flag"),
        Field("jockey_code", 49, 5, "X", "99999", codec="code"),
        Field("jockey_name", 54, 12, "X"),
        Field("carried_weight", 66, 3, "9", "999", scale=10),
        Field("apprentice_code", 69, 1, "9", "9"),
        Field("going_code", 70, 2, "9", "99"),
        Field("weather_code", 72, 1, "9", "9"),
        Field("tyb_win_odds_snapshot", 73, 6, "Z", "ZZZ9.9", target="skip"),
        Field("tyb_place_odds_snapshot", 79, 6, "Z", "ZZZ9.9", target="skip"),
        Field("odds_taken_at", 85, 4, "9", "9999", target="skip"),
        Field("weight", 89, 3, "9", "999"),
        Field("weight_diff", 92, 3, "X", "XZ9", codec="signed"),
        Field("odds_mark", 95, 1, "X", target="skip"),
        Field("paddock_mark", 96, 1, "X", target="skip"),
        Field("sogo_mark", 97, 1, "X", target="skip"),
        Field("batai_code", 98, 1, "X", target="skip"),
        Field("kehai_code", 99, 1, "X", target="skip"),
        Field("post_time", 100, 4, "9", "9999", codec="hhmm"),
        Field("reserved_104", 104, 23, "X", target="skip"),
    ),
)
