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
FRAME = 31  # col32 枠番
HORSE_NUMBER = 32  # col33 馬番
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
