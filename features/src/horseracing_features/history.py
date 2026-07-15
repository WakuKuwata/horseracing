"""Leak-safe past-performance features (research R1/R2/R3).

All history features for race R use only the horse's appearances with
``race_date < R`` (strictly before the day; same-day excluded). Two mechanisms,
both strict-before-date:
- cumulative aggregates: daily-aggregate then (cumsum - current-day) per horse.
- "previous" lookups: merge_asof(direction=backward, allow_exact_matches=False).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames

_HISTORY_COLUMNS = [
    "career_starts", "days_since_last", "prev_finish", "prev_last3f", "avg_finish", "win_rate",
    "cancel_count", "exclude_count", "stop_count",
    "prev_was_cancel", "prev_was_exclude", "prev_was_stop",
    "has_past_race", "is_debut", "past_race_count", "is_low_history",
]


def _runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "last_3f", "result_status"]]
    runs = (
        frames.race_horses[["race_id", "horse_id", "entry_status"]]
        .merge(races, on="race_id", how="left")
        .merge(rr, on=["race_id", "horse_id"], how="left")
    )
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    runs["is_finished"] = (runs["result_status"] == ResultStatus.FINISHED).astype(int)
    runs["is_win"] = ((runs["is_finished"] == 1) & (runs["finish_order"] == 1)).astype(int)
    runs["finish_for_avg"] = np.where(runs["is_finished"] == 1, runs["finish_order"], np.nan)
    runs["is_cancel"] = (runs["entry_status"] == EntryStatus.CANCELLED).astype(int)
    runs["is_exclude"] = (runs["entry_status"] == EntryStatus.EXCLUDED).astype(int)
    runs["is_stop"] = (runs["result_status"] == ResultStatus.STOPPED).astype(int)
    return runs


def _cumulative_before(runs: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Per (horse, date) cumulative aggregates strictly before that date."""
    daily = runs.groupby(["horse_id", "race_date"], as_index=False).agg(
        d_started=("is_started", "sum"),
        d_wins=("is_win", "sum"),
        d_finish_sum=("finish_for_avg", "sum"),
        d_finish_cnt=("finish_for_avg", "count"),
        d_cancel=("is_cancel", "sum"),
        d_exclude=("is_exclude", "sum"),
        d_stop=("is_stop", "sum"),
    ).sort_values(["horse_id", "race_date"], kind="stable")

    g = daily.groupby("horse_id", sort=False)
    _cum_cols = ["d_started", "d_wins", "d_finish_sum", "d_finish_cnt",
                 "d_cancel", "d_exclude", "d_stop"]
    for col in _cum_cols:
        daily[f"{col}_before"] = g[col].cumsum() - daily[col]

    daily["career_starts"] = daily["d_started_before"].astype("int64")
    daily["cancel_count"] = daily["d_cancel_before"].astype("int64")
    daily["exclude_count"] = daily["d_exclude_before"].astype("int64")
    daily["stop_count"] = daily["d_stop_before"].astype("int64")
    fin_cnt = daily["d_finish_cnt_before"]
    daily["avg_finish"] = np.where(fin_cnt > 0, daily["d_finish_sum_before"] / fin_cnt, np.nan)
    daily["win_rate"] = np.where(fin_cnt > 0, daily["d_wins_before"] / fin_cnt, np.nan)

    keep = ["horse_id", "race_date", "career_starts", "cancel_count", "exclude_count",
            "stop_count", "avg_finish", "win_rate"]
    return targets.merge(daily[keep], on=["horse_id", "race_date"], how="left")


def _previous_lookups(runs: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    t = targets.sort_values("race_date", kind="stable")

    finished = (
        runs[runs["is_finished"] == 1][["horse_id", "race_date", "finish_order", "last_3f"]]
        .rename(columns={"finish_order": "prev_finish", "last_3f": "prev_last3f"})
        .sort_values("race_date", kind="stable")
    )
    out = pd.merge_asof(t, finished, on="race_date", by="horse_id",
                        direction="backward", allow_exact_matches=False)
    # prev_finish comes from int finish_order; merge_asof leaves NaN for no-prev (float64 in the
    # full pool, which always has debuts). Pin float64 so a projected set with no debut stays byte-
    # identical to the full dtype (Feature 072). prev_last3f is already float64 (last_3f is float).
    out["prev_finish"] = out["prev_finish"].astype("float64")

    started = runs[runs["is_started"] == 1][["horse_id", "race_date"]].copy()
    started["last_started"] = started["race_date"]
    started = started.sort_values("race_date", kind="stable")
    out = pd.merge_asof(out, started, on="race_date", by="horse_id",
                        direction="backward", allow_exact_matches=False)
    # ``.dt.days`` yields int64 when no gap is NaT and float64 otherwise; the full pool always has a
    # debut (NaT) so it is float64. Pin float64 so a projected build whose target horses all have
    # history (no NaT) stays byte-identical to the full build's dtype (Feature 072, INV-P1).
    out["days_since_last"] = (out["race_date"] - out["last_started"]).dt.days.astype("float64")

    appear = runs.groupby(["horse_id", "race_date"], as_index=False).agg(
        prev_was_cancel=("is_cancel", "max"),
        prev_was_exclude=("is_exclude", "max"),
        prev_was_stop=("is_stop", "max"),
    ).sort_values("race_date", kind="stable")
    out = pd.merge_asof(out, appear, on="race_date", by="horse_id",
                        direction="backward", allow_exact_matches=False)
    for c in ["prev_was_cancel", "prev_was_exclude", "prev_was_stop"]:
        out[c] = out[c].fillna(0).astype("int64")
    return out.drop(columns=["last_started"])


def build_history_features(
    frames: Frames, *, low_history_max: int = 2, target_race_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Return per (race_id, horse_id) history features (as-of race_date < R).

    Feature 072: purely per-horse (cumulative-before + previous-lookup both grouped/merged by
    horse_id). ``target_race_ids`` restricts output to those races and the source to the target
    horses — byte-identical on those rows (INV-P1)."""
    runs = _runs(frames)
    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)]
        runs = runs[runs["horse_id"].isin(frozenset(targets["horse_id"]))]

    cum = _cumulative_before(runs, targets)
    prev = _previous_lookups(runs, targets)
    out = cum.merge(prev.drop(columns=["race_date"]), on=["race_id", "horse_id"], how="left")

    out["past_race_count"] = out["career_starts"].astype("int64")
    out["has_past_race"] = (out["career_starts"] > 0).astype("int64")
    out["is_debut"] = (out["career_starts"] == 0).astype("int64")
    out["is_low_history"] = (
        (out["career_starts"] >= 1) & (out["career_starts"] <= low_history_max)
    ).astype("int64")

    return out[["race_id", "horse_id", *_HISTORY_COLUMNS]]
