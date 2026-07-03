"""Pre-race static features (race conditions, horse attrs, weight/frame; Feature 030 斤量/季節)."""

from __future__ import annotations

import numpy as np
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
    race_cols = list(_RACE_COLS)
    # Feature 055: prize_money (pre-published race condition) — optional so pre-055 fixtures work
    has_prize = "prize_money" in frames.races.columns
    if has_prize:
        race_cols.append("prize_money")
    races = frames.races[["race_id", "race_date", *race_cols]]
    rh = frames.race_horses[
        ["race_id", "horse_id", "jockey_weight", "entry_status", *_HORSE_COLS]
    ]
    out = rh.merge(races, on="race_id", how="left")
    # Feature 020: field_size = number of started horses in the race (the race's own entries; no
    # result leak — entries are known pre-race).
    started = frames.race_horses[frames.race_horses["entry_status"] == EntryStatus.STARTED]
    field_size = started.groupby("race_id").size().rename("field_size").reset_index()
    out = out.merge(field_size, on="race_id", how="left")

    # Feature 030: handicap (斤量, all pre-race known). float64 (jockey_weight is Numeric/Decimal);
    # body weight 0/missing → ratio NaN (no impute). rel = 斤量 − race mean over started horses.
    cw = pd.to_numeric(out["jockey_weight"], errors="coerce").astype("float64")
    body = pd.to_numeric(out["weight"], errors="coerce").astype("float64")
    out["carried_weight"] = cw
    out["carried_weight_ratio"] = np.where(body > 0, cw / body, np.nan)
    cw_mean = (
        out[out["entry_status"] == EntryStatus.STARTED]
        .assign(_cw=cw[out["entry_status"] == EntryStatus.STARTED])
        .groupby("race_id")["_cw"].mean().rename("_cw_mean")
    )
    out = out.merge(cw_mean, on="race_id", how="left")
    out["carried_weight_rel"] = (out["carried_weight"] - out["_cw_mean"]).astype("float64")

    # Feature 030: season from race_date (静的). season: winter(Dec-Feb)=0/spring1/summer2/autumn3.
    month = pd.to_datetime(out["race_date"]).dt.month
    out["race_month"] = month.astype("float64")
    out["race_season"] = ((month % 12) // 3).astype("float64")

    # Feature 055: prize level (log scale; NaN-propagating) + bloodline lines (static categoricals)
    if has_prize:
        out["prize_money_log"] = np.log1p(
            pd.to_numeric(out["prize_money"], errors="coerce")
        ).astype("float64")
        out = out.drop(columns=["prize_money"])
    else:
        out["prize_money_log"] = np.nan
    line_cols = ["horse_id"]
    for c in ("sire_line", "damsire_line"):
        if c in frames.horses.columns:
            line_cols.append(c)
    lines = frames.horses[line_cols].copy()
    for c in ("sire_line", "damsire_line"):
        if c not in lines.columns:
            lines[c] = np.nan
    out = out.merge(lines, on="horse_id", how="left")

    # drop helpers not in the registry — esp. race_date/entry_status which assemble re-merges later
    # (leaving them here would create _x/_y suffix collisions).
    return out.drop(columns=["race_date", "entry_status", "jockey_weight", "_cw_mean"])
