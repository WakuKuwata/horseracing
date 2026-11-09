"""SED 成績データ — 第４版a (2022.08.22). Transcribed from the public spec
``https://jrdb.com/program/Sed/sed_doc.txt`` (fetched 2026-11-08). Record length 376.

``canonical_only`` (contracts §4 ✗ — same column name, different definition):
前３Ｆタイム (JRDB estimates it from position outside 1200 m), コーナー順位１〜４ (packed from the
first corner, ±1 at the first corner), レース脚質 (JRDB's own vocabulary 1〜6).
``skip``: every ＪＲＤＢデータ index, 1(2)着馬名, 備考, 確定複勝オッズ下, 10 時単勝/複勝オッズ
(no single-latest-value home in the DB, 憲法 V), 前/後３Ｆ先頭差, コース, 本賞金/収得賞金 (a horse's
earnings, not the race's 1 着賞金), ペース流れ, ４角コース取り.
"""

from __future__ import annotations

from . import Field, Layout, race_key_fields

LAYOUT = Layout(
    kind="SED",
    version="4",
    record_length=376,
    doc="https://jrdb.com/program/Sed/sed_doc.txt 第４版a 2022.08.22 (fetched 2026-11-08)",
    fields=(
        *race_key_fields(),
        Field("horse_number", 9, 2, "9", "99"),
        Field("blood_reg_no", 11, 8, "X", codec="code"),
        Field("race_date", 19, 8, "9", "9", codec="yyyymmdd"),
        Field("horse_name", 27, 36, "X"),
        Field("distance", 63, 4, "9", "9999"),
        Field("track_code", 67, 1, "9", "9"),
        Field("turn_code", 68, 1, "9", "9"),
        Field("inout_code", 69, 1, "9", "9"),
        Field("going_code", 70, 2, "9", "99"),  # 10〜42
        Field("shubetsu_code", 72, 2, "9", "99"),
        Field("joken_code", 74, 2, "X", "XX", codec="code"),
        Field("kigo_code", 76, 3, "X", "999", codec="code"),
        Field("juryo_code", 79, 1, "9", "9"),
        Field("grade_code", 80, 1, "9", "9"),
        Field("race_name", 81, 50, "X"),
        Field("head_count", 131, 2, "9", "99"),
        Field("race_name_short", 133, 8, "X"),
        Field("finish_order", 141, 2, "9", "99"),
        Field(
            "abnormal_code", 143, 1, "9", "9"
        ),  # 0 なし 1 取消 2 除外 3 中止 4 失格 5 降着 6 再騎乗
        Field("finish_time", 144, 4, "9", "9999", codec="time_m_sss"),
        Field("jockey_weight", 148, 3, "9", "999", scale=10),
        Field("jockey_name", 151, 12, "X"),
        Field("trainer_name", 163, 12, "X"),
        Field("final_win_odds", 175, 6, "Z", "ZZZ9.9"),
        Field("final_win_popularity", 181, 2, "9", "99"),
        Field("jrdb_indices", 183, 61, "Z", target="skip"),  # ＩＤＭ〜レースＰ指数 (相対 183〜243)
        Field("winner_name", 244, 12, "X", target="skip"),  # 1(2)着馬名
        Field("finish_time_diff", 256, 3, "9", "999", scale=10),  # 1(2)着タイム差 0.1 秒
        Field("first_3f", 259, 3, "9", "999", scale=10, target="canonical_only"),
        Field("last_3f", 262, 3, "9", "999", scale=10),
        Field("biko", 265, 24, "X", target="skip"),
        Field("reserved_289", 289, 2, "X", target="skip"),
        Field("final_place_odds_low", 291, 6, "Z", "ZZZ9.9", target="skip"),  # 保存先なし (V)
        Field("odds10_win", 297, 6, "Z", "ZZZ9.9", target="skip"),  # 10 時単勝オッズ
        Field("odds10_place", 303, 6, "Z", "ZZZ9.9", target="skip"),  # 10 時複勝オッズ
        Field("corner_1", 309, 2, "9", "99", target="canonical_only"),
        Field("corner_2", 311, 2, "9", "99", target="canonical_only"),
        Field("corner_3", 313, 2, "9", "99", target="canonical_only"),
        Field("corner_4", 315, 2, "9", "99", target="canonical_only"),
        Field("first_3f_lead_diff", 317, 3, "9", "99", target="skip"),
        Field("last_3f_lead_diff", 320, 3, "9", "99", target="skip"),
        Field("jockey_code", 323, 5, "X", "99999", codec="code"),
        Field("trainer_code", 328, 5, "X", "99999", codec="code"),
        Field("weight", 333, 3, "9", "999"),
        Field("weight_diff", 336, 3, "X", "XZ9", codec="signed"),
        Field("weather_code", 339, 1, "9", "9"),
        Field("course", 340, 1, "X", target="skip"),
        Field("running_style_code", 341, 1, "X", "X", codec="code", target="canonical_only"),
        Field("win_payout_yen", 342, 7, "Z", "ZZZZZZ9"),
        Field("place_payout_yen", 349, 7, "Z", "ZZZZZZ9"),
        Field("honshokin_man", 356, 5, "Z", "ZZZZ9", target="skip"),  # 馬が得た本賞金
        Field("shutoku_man", 361, 5, "Z", "ZZZZ9", target="skip"),
        Field("race_pace_flow", 366, 2, "9", target="skip"),
        Field("uma_pace_flow", 368, 2, "9", target="skip"),
        Field("corner4_course", 370, 1, "9", target="skip"),
        Field("post_time", 371, 4, "9", "9999", codec="hhmm"),  # 第４版a 以降; older archives blank
    ),
)
