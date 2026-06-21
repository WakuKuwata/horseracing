"""Helper to author small Shift_JIS (cp932) golden CSV fixtures for tests.

`make_row` builds a 73-field JRA-VAN record from high-level kwargs (defaults make
a valid finisher); override fields to model cancelled/excluded/stopped/etc.
"""

from __future__ import annotations

import csv
from pathlib import Path

from horseracing_ingest import layout

_DEFAULTS = {
    # race-level
    layout.YEAR: "07",
    layout.RACE_DATE: "2007.8.11",
    layout.KAI: "1",
    layout.VENUE_NAME: "札幌",
    layout.NICHIME: "1",
    layout.RACE_NUMBER: "1",
    layout.RACE_NAME_SHORT: "未勝利",
    layout.RACE_NAME_FULL: "",
    layout.RACE_CLASS: "未勝利",
    layout.GRADE: "",
    layout.TRACK_TYPE: "芝",
    layout.DISTANCE: "1500",
    layout.GOING: "良",
    layout.WEATHER: "晴",
    # horse-level (finisher defaults)
    layout.RACE_HORSE_ID_18: "200708110101010101",
    layout.FRAME: "1",
    layout.HORSE_NUMBER: "1",
    layout.HORSE_NAME: "テスト馬",
    layout.SEX: "牡",
    layout.AGE: "3",
    layout.JOCKEY_NAME: "騎手太郎",
    layout.JOCKEY_WEIGHT: "55.0",
    layout.FINISH_ORDER: "1",
    layout.TIME_DIFF: "0.0",
    layout.POPULARITY: "1",
    layout.ODDS: "2.5",
    layout.FINISH_TIME: "1.29.9",
    layout.CORNER_1: "0",
    layout.CORNER_2: "2",
    layout.CORNER_3: "3",
    layout.CORNER_4: "1",
    layout.RUNNING_STYLE: "先行",
    layout.LAST_3F: "36.1",
    layout.HORSE_WEIGHT: "460",
    layout.WEIGHT_DIFF: "0",
    layout.TRAINER_NAME: "調教師花子",
    layout.BLOOD_REG_NO: "2005109144",
    layout.JOCKEY_CODE: "01102",
    layout.TRAINER_CODE: "01084",
    layout.SIRE_NAME: "父馬",
    layout.DAM_NAME: "母馬",
    layout.DAMSIRE_NAME: "母父馬",
    layout.BIRTH_DATE: "20050419",
}

# Friendly kwarg name -> column index.
_KW = {
    "year2": layout.YEAR,
    "race_date": layout.RACE_DATE,
    "kai": layout.KAI,
    "venue": layout.VENUE_NAME,
    "nichime": layout.NICHIME,
    "race_no": layout.RACE_NUMBER,
    "race_name_short": layout.RACE_NAME_SHORT,
    "race_class": layout.RACE_CLASS,
    "grade": layout.GRADE,
    "track_type": layout.TRACK_TYPE,
    "distance": layout.DISTANCE,
    "going": layout.GOING,
    "weather": layout.WEATHER,
    "id18": layout.RACE_HORSE_ID_18,
    "frame": layout.FRAME,
    "horse_number": layout.HORSE_NUMBER,
    "horse_name": layout.HORSE_NAME,
    "sex": layout.SEX,
    "age": layout.AGE,
    "jockey_name": layout.JOCKEY_NAME,
    "jockey_weight": layout.JOCKEY_WEIGHT,
    "finish_order": layout.FINISH_ORDER,
    "time_diff": layout.TIME_DIFF,
    "popularity": layout.POPULARITY,
    "odds": layout.ODDS,
    "finish_time": layout.FINISH_TIME,
    "corner1": layout.CORNER_1,
    "corner2": layout.CORNER_2,
    "corner3": layout.CORNER_3,
    "corner4": layout.CORNER_4,
    "running_style": layout.RUNNING_STYLE,
    "last_3f": layout.LAST_3F,
    "horse_weight": layout.HORSE_WEIGHT,
    "weight_diff": layout.WEIGHT_DIFF,
    "trainer_name": layout.TRAINER_NAME,
    "horse_id": layout.BLOOD_REG_NO,
    "jockey_code": layout.JOCKEY_CODE,
    "trainer_code": layout.TRAINER_CODE,
    "sire_name": layout.SIRE_NAME,
    "dam_name": layout.DAM_NAME,
    "damsire_name": layout.DAMSIRE_NAME,
    "birth_date": layout.BIRTH_DATE,
}


def make_row(**kw) -> list[str]:
    """Return a 73-field record. Unknown kwargs raise (typo safety)."""
    fields = [""] * layout.EXPECTED_COLUMNS
    for idx, val in _DEFAULTS.items():
        fields[idx] = val
    for key, val in kw.items():
        if key not in _KW:
            raise KeyError(f"unknown make_row field: {key}")
        fields[_KW[key]] = "" if val is None else str(val)
    return fields


def write_csv(path: str | Path, rows: list[list[str]]) -> Path:
    path = Path(path)
    with open(path, "w", encoding="cp932", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
    return path
