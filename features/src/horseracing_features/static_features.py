"""Pre-race static features (race conditions, horse attributes, weight/frame)."""

from __future__ import annotations

import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames

_RACE_COLS = [
    "venue_code", "distance", "track_type", "going", "weather", "race_class", "race_number",
]
_HORSE_COLS = [
    "age", "sex", "frame", "horse_number", "jockey_id", "trainer_id", "weight", "weight_diff",
]


def build_static_features(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", *_RACE_COLS]]
    rh = frames.race_horses[["race_id", "horse_id", *_HORSE_COLS]]
    out = rh.merge(races, on="race_id", how="left")
    # Feature 020: field_size = number of started horses in the race (the race's own entries; no
    # result leak — entries are known pre-race).
    started = frames.race_horses[frames.race_horses["entry_status"] == EntryStatus.STARTED]
    field_size = started.groupby("race_id").size().rename("field_size").reset_index()
    return out.merge(field_size, on="race_id", how="left")
