"""Feature 055: owner / breeder cross-entity as-of rates (human_form 同型).

The raw CSV carries 馬主名/生産者名 at ~100% coverage — high-cardinality entities the model has
never seen (jockey/trainer proved the entity-form pattern in 020/036). Keys are NFKC-normalized
NAMES (no ID columns exist in the source, 026 precedent); owner is a horses static column
(last-write-wins — a transferred horse attributes its past runs to the CURRENT owner, which is
known pre-race, so no future information enters; documented limitation, research D2).

CRITICAL leak boundary (020 codex): cross-entity statistics use the daily (cumsum − current-day)
mechanism — subtracting the whole target day removes the target run AND every same-day run of
that owner/breeder. Only finished prior runs count. Rates with fewer than MIN_STARTS prior
finished runs are NaN (Unknown ≠ 0, 憲法 IV).
"""

from __future__ import annotations

import unicodedata

import numpy as np
import pandas as pd
from horseracing_db.enums import ResultStatus

from .loader import Frames

OWNER_BREEDER_COLUMNS = [
    "asof_owner_win_rate", "asof_owner_place_rate", "asof_breeder_win_rate",
]

#: minimum prior finished runs for a rate; below → NaN (fixed module constant = feature definition)
MIN_STARTS = 20


def _normalize_name(s: pd.Series) -> pd.Series:
    return s.map(lambda v: unicodedata.normalize("NFKC", v).strip() if isinstance(v, str) else v)


def _runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id"]]
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    horse_cols = ["horse_id"]
    for c in ("owner_name", "breeder_name"):
        if c in frames.horses.columns:
            horse_cols.append(c)
    horses = frames.horses[horse_cols].copy()
    for c in ("owner_name", "breeder_name"):
        if c in horses.columns:
            horses[c] = _normalize_name(horses[c])
        else:  # pre-055 fixtures -> all-NaN features
            horses[c] = np.nan

    runs = (
        rh.merge(races, on="race_id", how="left")
        .merge(rr, on=["race_id", "horse_id"], how="left")
        .merge(horses, on="horse_id", how="left")
    )
    runs["is_finished"] = (runs["result_status"] == ResultStatus.FINISHED).astype(int)
    runs["is_win"] = ((runs["is_finished"] == 1) & (runs["finish_order"] == 1)).astype(int)
    runs["is_place"] = ((runs["is_finished"] == 1) & (runs["finish_order"] <= 3)).astype(int)
    runs["finish_for_cnt"] = np.where(runs["is_finished"] == 1, 1.0, np.nan)
    return runs


def _rate_before(runs: pd.DataFrame, key: str, num_col: str, out_col: str) -> pd.DataFrame:
    """Per (key, date): numerator/finished-count over runs STRICTLY before that date."""
    sub = runs[runs[key].notna()]
    daily = sub.groupby([key, "race_date"], as_index=False).agg(
        d_num=(num_col, "sum"),
        d_cnt=("finish_for_cnt", "count"),
    ).sort_values([key, "race_date"], kind="stable")
    g = daily.groupby(key, sort=False)
    daily["num_b"] = g["d_num"].cumsum() - daily["d_num"]  # excludes the whole current day
    daily["cnt_b"] = g["d_cnt"].cumsum() - daily["d_cnt"]
    daily[out_col] = np.where(
        daily["cnt_b"] >= MIN_STARTS, daily["num_b"] / daily["cnt_b"], np.nan
    )
    return daily[[key, "race_date", out_col]]


def build_owner_breeder_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) owner/breeder as-of rates (target-day excluded; float64)."""
    runs = _runs(frames)
    targets = runs[["race_id", "horse_id", "race_date", "owner_name", "breeder_name"]].copy()

    o_win = _rate_before(runs, "owner_name", "is_win", "asof_owner_win_rate")
    o_plc = _rate_before(runs, "owner_name", "is_place", "asof_owner_place_rate")
    b_win = _rate_before(runs, "breeder_name", "is_win", "asof_breeder_win_rate")

    out = (
        targets
        .merge(o_win, on=["owner_name", "race_date"], how="left")
        .merge(o_plc, on=["owner_name", "race_date"], how="left")
        .merge(b_win, on=["breeder_name", "race_date"], how="left")
    )
    for c in OWNER_BREEDER_COLUMNS:
        out[c] = out[c].astype("float64")
    return out[["race_id", "horse_id", *OWNER_BREEDER_COLUMNS]]
