"""BAC 番組データ — 第４版d (2017.12.30). Transcribed from the public spec
``https://jrdb.com/program/Bac/bac_doc.txt`` (fetched 2026-11-08).

Note: the doc header says レコード長 176 but its own last item (改行 at 相対 183, 2 bytes) and the
public sample ``BAC080913.txt`` give 184 bytes per record; 184 is used.
"""

from __future__ import annotations

from . import Field, Layout, race_key_fields

LAYOUT = Layout(
    kind="BAC",
    version="4",
    record_length=184,
    doc="https://jrdb.com/program/Bac/bac_doc.txt 第４版d 2017.12.30 (fetched 2026-11-08)",
    fields=(
        *race_key_fields(),
        Field("race_date", 9, 8, "9", "9", codec="yyyymmdd"),
        Field("post_time", 17, 4, "X", "X", codec="hhmm"),
        Field("distance", 21, 4, "9", "9999"),
        Field("track_code", 25, 1, "9", "9"),  # 1 芝 2 ダート 3 障害
        Field("turn_code", 26, 1, "9", "9"),  # 1 右 2 左 3 直 9 他
        Field("inout_code", 27, 1, "9", "9"),  # 1 通常(内) 2 外 3 直ダ 9 他
        Field("shubetsu_code", 28, 2, "9", "99"),  # 種別 → コード表
        Field(
            "joken_code", 30, 2, "X", "XX", codec="code"
        ),  # 条件 04/05/08/09/10/15/16/A1/A2/A3/OP
        Field("kigo_code", 32, 3, "X", "999", codec="code"),  # 記号
        Field("juryo_code", 35, 1, "9", "9"),  # 重量
        Field("grade_code", 36, 1, "9", "9"),  # グレード 1 G1 … 6 L
        Field("race_name", 37, 50, "X"),  # 全角 25
        Field("kaisu", 87, 8, "X", target="skip"),  # 回数(第ZZ9回・全角半角混在)
        Field("head_count", 95, 2, "9", "99"),
        Field("course", 97, 1, "X", target="skip"),  # コース A/A1/A2/B/C/D(列が無い)
        Field("kaisai_kubun", 98, 1, "X", target="skip"),  # 1 関東 2 関西 3 ローカル
        Field("race_name_short", 99, 8, "X"),  # 全角 4
        Field("race_name_9", 107, 18, "X"),  # 全角 9
        Field("data_kubun", 125, 1, "X", codec="code"),  # 1 特別登録 2 想定確定 3 前日
        Field("prize_1_man", 126, 5, "Z", "ZZZZ9"),
        Field("prize_2_man", 131, 5, "Z", "ZZZZ9"),
        Field("prize_3_man", 136, 5, "Z", "ZZZZ9"),
        Field("prize_4_man", 141, 5, "Z", "ZZZZ9"),
        Field("prize_5_man", 146, 5, "Z", "ZZZZ9"),
        Field("sannyu_1_man", 151, 5, "Z", "ZZZZ9", target="skip"),  # 算入賞金
        Field("sannyu_2_man", 156, 5, "Z", "ZZZZ9", target="skip"),
        Field("sale_flags", 161, 16, "X", codec="code"),  # 馬券発売フラグ 16 桁
        Field("win5_flag", 177, 1, "Z", target="skip"),
        Field("yobi", 178, 5, "X", target="skip"),
    ),
)
