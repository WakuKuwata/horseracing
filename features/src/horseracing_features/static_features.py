"""Pre-race static features (race conditions, horse attributes, weight/frame)."""

from __future__ import annotations

import pandas as pd

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
    return rh.merge(races, on="race_id", how="left")
