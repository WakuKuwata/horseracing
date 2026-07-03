"""JRA-VAN year-CSV column layout (research R1) and venue table (R3).

Column constants are 0-indexed (research R1 is 1-indexed → index = number - 1).
Only the columns the core schema needs are named; the rest are skipped.
"""

from __future__ import annotations

EXPECTED_COLUMNS = 73

# --- race-level ---
YEAR = 0  # col1, 2-digit
RACE_DATE = 3  # col4, "YYYY.M.D"
KAI = 4  # col5, 開催回
VENUE_NAME = 5  # col6
NICHIME = 6  # col7, 日目
RACE_NUMBER = 7  # col8
RACE_NAME_SHORT = 8  # col9
RACE_NAME_FULL = 9  # col10 (full-width padded)
RACE_CLASS = 10  # col11
GRADE = 12  # col13 (empty = no grade)
TRACK_TYPE = 13  # col14 (芝/ダ)
DISTANCE = 17  # col18
GOING = 19  # col20 (馬場)
WEATHER = 20  # col21

# --- horse-level ---
RACE_HORSE_ID_18 = 30  # col31, 18-digit id (cross-check only)
# NOTE: actual JRA-VAN file order is 馬番 (index31, unique 1..N) then 枠番 (index32, 1-8 bracket) —
# verified against raw rows. (Fixed: these two were previously swapped, so horse_number held the
# 枠番 and collided for ~97% of races, breaking canonical field / joint / market-q.)
HORSE_NUMBER = 31  # col32 馬番 (unique per race)
FRAME = 32  # col33 枠番 (1-8 bracket)
HORSE_NAME = 33  # col34
SEX = 34  # col35 (牡/牝/セ)
AGE = 35  # col36
JOCKEY_NAME = 36  # col37
JOCKEY_WEIGHT = 37  # col38 斤量
FINISH_ORDER = 39  # col40 (0 = non-finisher)
TIME_DIFF = 40  # col41 ("----" = non-finisher)
POPULARITY = 41  # col42 (result-time)
ODDS = 42  # col43 単勝 (result-time)
FINISH_TIME = 44  # col45 ("M.SS.s")
CORNER_1 = 47  # col48
CORNER_2 = 48  # col49
CORNER_3 = 49  # col50
CORNER_4 = 50  # col51
RUNNING_STYLE = 51  # col52 脚質
LAST_3F = 52  # col53 上がり3F
HORSE_WEIGHT = 56  # col57 馬体重
WEIGHT_DIFF = 57  # col58 増減
TRAINER_NAME = 58  # col59
BLOOD_REG_NO = 61  # col62 血統登録番号 -> horse_id
JOCKEY_CODE = 62  # col63 -> jockey_id
TRAINER_CODE = 63  # col64 -> trainer_id
SIRE_NAME = 66  # col67 父名
DAM_NAME = 67  # col68 母名
DAMSIRE_NAME = 68  # col69 母父名
BIRTH_DATE = 72  # col73 "YYYYMMDD"

# --- Feature 055: previously-unread columns (specs/055-raw-column-features) ---
# Semantics verified against the raw files (research.md): for 1200m (=6F) races
# finish_time == FIRST_3F + LAST_3F held for 100.000% of ~30k rows (2010/2018/2024);
# PRIZE_MONEY is race-constant (1着賞金 万円 — a pre-published race condition, not a result).
PRIZE_MONEY = 23  # col24 1着賞金(万円)
FIRST_3F = 54  # col55 テン3F(前半3ハロン秒)
OWNER_NAME = 64  # col65 馬主名
BREEDER_NAME = 65  # col66 生産者名
SIRE_LINE = 69  # col70 父系統
DAMSIRE_LINE = 70  # col71 母父系統

CORNER_COLUMNS = (CORNER_1, CORNER_2, CORNER_3, CORNER_4)

# --- venue name -> 2-char JRA course code (R3) ---
VENUE_CODE: dict[str, str] = {
    "札幌": "01",
    "函館": "02",
    "福島": "03",
    "新潟": "04",
    "東京": "05",
    "中山": "06",
    "中京": "07",
    "京都": "08",
    "阪神": "09",
    "小倉": "10",
}
