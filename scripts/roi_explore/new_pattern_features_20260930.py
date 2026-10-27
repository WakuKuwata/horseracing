"""Past-only exploratory features from the bounded 2007+ input; no I/O or model fitting.

Horse identity is the exact stored horse_id. History counts include all earlier
started rows in the supplied input, including non-finishers. A finished top-three
result contributes to the top-three numerator; the denominator is prior starts.
Dates and supplied days_since_last are calendar dates/days, not meeting-day counts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NEW_COLUMNS = (
    "prev_gap_days", "prev2_gap_days",
    "surface_starts", "surface_wins", "surface_top3_rate",
    "distband_starts", "distband_wins", "distband_top3_rate",
    "course_starts", "course_wins", "course_top3_rate",
    "return_to_surface", "return_to_course", "rest_second", "rest_third",
    "pace_front_count", "pace_front_share", "pace_front_others", "pace_known_count",
    "odds_ratio_prev",
)

REQUIRED_COLUMNS = (
    "race_id", "horse_id", "race_date", "days_since_last", "track_type", "dist_band",
    "venue_code", "won", "finish_order", "result_status", "prev_track_type",
    "prev_venue_code", "prev_running_style", "odds", "prev_odds",
)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with 20 added columns, preserving input order/index/columns.

Known condition groups start at starts=0, wins=0, top3_rate=NaN. Missing group
keys leave all three history fields NaN. The ``return_to_*`` flags follow the
requested change predicate only; they do not assert a prior visit to the new
condition (combine them with the corresponding starts>=1 for that meaning).

pace_front_share is the number of observed prior front styles divided by the
entire race field. Use pace_known_count to assess missing prior-style coverage.
No current-race running_style column is read.
"""
    missing = set(REQUIRED_COLUMNS).difference(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    collisions = set(NEW_COLUMNS).intersection(df.columns)
    if collisions:
        raise ValueError(f"Refusing to replace existing columns: {sorted(collisions)}")
    out = df.copy()
    if df.empty:
        for column in NEW_COLUMNS:
            out[column] = pd.Series(index=out.index, dtype="bool" if column.startswith(("return_", "rest_")) else "float64")
        return out
    work = df.loc[:, REQUIRED_COLUMNS].copy().reset_index(drop=True)
    dates = pd.to_datetime(work.race_date, errors="raise")
    assert dates.notna().all(), "Missing race_date is not a valid history key"
    assert dates.min() >= pd.Timestamp("2007-01-01"), "History includes pre-2007 rows"
    assert work.horse_id.notna().all() and work.race_id.notna().all(), "Missing identity"
    calendar_dates = dates.dt.normalize()
    assert not pd.DataFrame({"horse_id": work.horse_id, "date": calendar_dates}).duplicated().any(), "Same horse has multiple starts on one calendar date"
    work["race_date"] = calendar_dates
    # A temporary RangeIndex lets us restore original order even with a non-unique input index.
    work = work.sort_values(["horse_id", "race_date", "race_id"], kind="stable")
    original_positions = work.index.to_numpy()
    work = work.reset_index(drop=True)
    result: dict[str, pd.Series] = {}
    gh = work.groupby("horse_id", sort=False)
    gap = pd.to_numeric(work.days_since_last, errors="coerce")
    work["_gap"] = gap
    result["prev_gap_days"] = gh["_gap"].shift(1)
    result["prev2_gap_days"] = gh["_gap"].shift(2)

    # Current outcomes are subtracted before any row receives its cumulative history.
    work["_win"] = work.won.eq(True).astype(np.int64)
    work["_top3"] = (work.result_status.eq("finished") & work.finish_order.between(1, 3)).astype(np.int64)
    for prefix, keys in (
        ("surface", ["horse_id", "track_type"]),
        ("distband", ["horse_id", "track_type", "dist_band"]),
        ("course", ["horse_id", "venue_code", "track_type"]),
    ):
        groups = work.groupby(keys, sort=False, dropna=True, observed=True)
        starts = groups.cumcount().astype(float)
        wins = groups["_win"].cumsum() - work["_win"]
        top3 = groups["_top3"].cumsum() - work["_top3"]
        result[f"{prefix}_starts"] = starts
        result[f"{prefix}_wins"] = wins.astype(float)
        result[f"{prefix}_top3_rate"] = top3.div(starts.where(starts > 0))

    result["return_to_surface"] = (work.prev_track_type.notna() & work.track_type.notna()
                                    & work.prev_track_type.ne(work.track_type))
    result["return_to_course"] = (work.prev_venue_code.notna() & work.venue_code.notna()
                                   & work.prev_venue_code.ne(work.venue_code))
    mid = gap.between(14, 56, inclusive="both")
    result["rest_second"] = (result["prev_gap_days"].ge(84) & mid)
    result["rest_third"] = (result["prev2_gap_days"].ge(84)
                            & result["prev_gap_days"].between(14, 56, inclusive="both") & mid)

    # Previous styles observed in the input are 逃げ,先行,中団,後方,差し,追込,ﾏｸﾘ.
    # Nonempty source styles are known; unknown/sentinel spellings remain unobserved.
    style = work.prev_running_style.astype("string").str.strip()
    known = style.notna() & ~style.isin(["", "-", "--", "不明", "Unknown", "unknown"])
    front = style.isin(["逃げ", "先行"])
    front_count = front.astype(float).groupby(work.race_id, sort=False).transform("sum")
    result["pace_front_count"] = front_count
    field_size = work.groupby("race_id", sort=False).race_id.transform("size")
    result["pace_front_share"] = front_count.div(field_size)
    result["pace_front_others"] = front_count - front.astype(float)
    result["pace_known_count"] = known.astype(float).groupby(work.race_id, sort=False).transform("sum")

    current_odds = pd.to_numeric(work.odds, errors="coerce")
    previous_odds = pd.to_numeric(work.prev_odds, errors="coerce")
    valid_odds = (np.isfinite(current_odds) & np.isfinite(previous_odds)
                  & current_odds.gt(0) & previous_odds.gt(0))
    result["odds_ratio_prev"] = current_odds.where(valid_odds).div(previous_odds.where(valid_odds))
    for column in NEW_COLUMNS:
        values = result[column].to_numpy()
        restored = np.empty_like(values)
        restored[original_positions] = values
        out[column] = restored
    return out
