"""KYI 競走馬データ — 第11版 (2023.06.25). Transcribed from the public spec
``https://jrdb.com/program/Kyi/kyi_doc.txt`` (fetched 2026-11-08). Record length 1024, and the
doc promises the length will not change in later minor revisions.

Adopted (``current``): race key, 馬番, 血統登録番号, 馬名, 騎手名, 負担重量, 見習い区分, 調教師名,
調教師所属, 枠番, 騎手コード, 調教師コード, 枠確定馬体重(増減), 取消フラグ, 性別, 馬主名.
Everything JRDB-estimated (ＩＤＭ・各指数・印・展開予想・基準オッズ・特定/総合情報) is ``skip``
(憲法 II: never a model feature; 憲法 V: 基準オッズ is not a market value).
"""

from __future__ import annotations

from . import Field, Layout, race_key_fields

LAYOUT = Layout(
    kind="KYI",
    version="11",
    record_length=1024,
    doc="https://jrdb.com/program/Kyi/kyi_doc.txt 第11版 2023.06.25 (fetched 2026-11-08)",
    fields=(
        *race_key_fields(),
        Field("horse_number", 9, 2, "9", "99"),
        Field("blood_reg_no", 11, 8, "X", codec="code"),
        Field("horse_name", 19, 36, "X"),
        Field("idm", 55, 5, "Z", "ZZ9.9", target="skip"),
        Field("jockey_index", 60, 5, "Z", "ZZ9.9", target="skip"),
        Field("info_index", 65, 5, "Z", "ZZ9.9", target="skip"),
        Field("reserved_index_1", 70, 5, "Z", "ZZ9.9", target="skip"),
        Field("reserved_index_2", 75, 5, "Z", "ZZ9.9", target="skip"),
        Field("reserved_index_3", 80, 5, "Z", "ZZ9.9", target="skip"),
        Field("sogo_index", 85, 5, "Z", "ZZ9.9", target="skip"),
        Field("predicted_style", 90, 1, "9", target="skip"),  # JRDB 脚質予想(DB の脚質とは別物)
        Field("distance_aptitude", 91, 1, "9", target="skip"),
        Field("josho_do", 92, 1, "9", target="skip"),
        Field("rotation", 93, 3, "Z", "ZZ9", target="skip"),
        Field("kijun_odds", 96, 5, "Z", "ZZ9.9", target="skip"),
        Field("kijun_popularity", 101, 2, "Z", "Z9", target="skip"),
        Field("kijun_place_odds", 103, 5, "Z", "ZZ9.9", target="skip"),
        Field("kijun_place_popularity", 108, 2, "Z", "Z9", target="skip"),
        Field("tokutei_marks", 110, 15, "Z", target="skip"),  # ◎○▲△× 各 3 桁
        Field("sogo_marks", 125, 15, "Z", target="skip"),
        Field("ninki_index", 140, 5, "Z", "ZZZZ9", target="skip"),
        Field("chokyo_index", 145, 5, "Z", "ZZ9.9", target="skip"),
        Field("kyusha_index", 150, 5, "Z", "ZZ9.9", target="skip"),
        Field("chokyo_arrow", 155, 1, "9", target="skip"),
        Field("kyusha_hyoka", 156, 1, "9", target="skip"),
        Field("jockey_rentai_rate", 157, 4, "Z", "Z9.9", target="skip"),
        Field("gekiso_index", 161, 3, "Z", "ZZ9", target="skip"),
        Field("hoof_code", 164, 2, "9", target="skip"),
        Field("omo_aptitude", 166, 1, "9", target="skip"),
        Field("class_code", 167, 2, "9", target="skip"),
        Field("reserved_169", 169, 2, "X", target="skip"),
        Field("blinker", 171, 1, "X", target="skip"),
        Field("jockey_name", 172, 12, "X"),  # 全角 6
        Field("carried_weight", 184, 3, "9", "999", scale=10),  # 0.1 kg
        Field("apprentice_code", 187, 1, "9", "9"),  # 1 ☆ 2 △ 3 ▲ 4 ★ 9 ◇
        Field("trainer_name", 188, 12, "X"),
        Field("trainer_affiliation", 200, 4, "X"),  # 全角 2
        Field("prev_result_keys", 204, 80, "9", target="skip"),  # 前走 1〜5 競走成績キー 16×5
        Field("prev_race_keys", 284, 40, "9", target="skip"),  # 前走 1〜5 レースキー 8×5
        Field("frame", 324, 1, "9", "9"),
        Field("reserved_325", 325, 2, "X", target="skip"),
        Field("marks", 327, 7, "9", target="skip"),  # 総合/ＩＤＭ/情報/騎手/厩舎/調教/激走 印
        Field("turf_aptitude_code", 334, 1, "X", target="skip"),
        Field("dirt_aptitude_code", 335, 1, "X", target="skip"),
        Field("jockey_code", 336, 5, "X", "99999", codec="code"),  # JRDB 独自コード
        Field("trainer_code", 341, 5, "X", "99999", codec="code"),
        Field("reserved_346", 346, 1, "X", target="skip"),
        Field("earned_prize_man", 347, 6, "Z", "ZZZZZ9", target="skip"),  # 獲得賞金(馬の累積)
        Field("shutoku_prize_man", 353, 5, "Z", "ZZZZ9", target="skip"),
        Field("joken_class", 358, 1, "9", target="skip"),
        Field("tenkai_indices", 359, 20, "Z", target="skip"),  # テン/ペース/上がり/位置 指数
        Field("pace_prediction", 379, 1, "X", target="skip"),
        Field("tenkai_positions", 380, 15, "Z", target="skip"),  # 道中/後3F/ゴール 順位・差・内外
        Field("tenkai_mark", 395, 1, "X", target="skip"),
        Field("distance_aptitude_2", 396, 1, "9", target="skip"),
        Field("weight", 397, 3, "9", "999"),  # 枠確定馬体重 (データ無はスペース)
        Field("weight_diff", 400, 3, "X", "XZ9", codec="signed"),
        Field("cancelled", 403, 1, "9", "9", codec="flag"),  # 1 取消
        Field("sex_code", 404, 1, "9", "9"),  # 1 牡 2 牝 3 セン
        Field("owner_name", 405, 40, "X"),  # 全角 20
        Field("owner_assoc_code", 445, 2, "9", target="skip"),
        Field("uma_kigo_code", 447, 2, "9", target="skip"),
        Field("index_ranks", 449, 12, "Z", target="skip"),  # 激走/LS/テン/ペース/上がり/位置 順位
        Field("jockey_win_rate", 461, 4, "Z", "Z9.9", target="skip"),
        Field("jockey_show_rate", 465, 4, "Z", "Z9.9", target="skip"),
        Field("transport_kubun", 469, 1, "X", target="skip"),
        Field("soho", 470, 8, "9", target="skip"),  # 走法
        Field("taikei", 478, 24, "X", target="skip"),  # 体型
        Field("taikei_sogo", 502, 9, "9", target="skip"),
        Field("uma_tokki", 511, 9, "9", target="skip"),
        Field("start_index", 520, 4, "Z", "Z9.9", target="skip"),
        Field("deokure_rate", 524, 4, "Z", "Z9.9", target="skip"),
        Field("reference_prev", 528, 2, "9", target="skip"),
        Field("reference_prev_jockey_code", 530, 5, "X", target="skip"),
        Field("manken_index", 535, 3, "Z", "ZZ9", target="skip"),
        Field("manken_mark", 538, 1, "9", target="skip"),
        Field("demotion_flag", 539, 1, "9", target="skip"),
        Field("gekiso_type", 540, 2, "X", target="skip"),
        Field("rest_reason_code", 542, 2, "9", target="skip"),
        Field("flags_16", 544, 16, "X", target="skip"),
        Field("nyukyu_nth", 560, 2, "Z", target="skip"),
        Field("nyukyu_date", 562, 8, "9", target="skip"),
        Field("nyukyu_days_before", 570, 3, "Z", target="skip"),
        Field("hoboku_saki", 573, 50, "X", target="skip"),
        Field("hoboku_rank", 623, 1, "X", target="skip"),
        Field("kyusha_rank", 624, 1, "9", target="skip"),
        Field("reserved_625", 625, 398, "X", target="skip"),
    ),
)
