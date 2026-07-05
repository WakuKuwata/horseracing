"""Feature 023: leak-safe pace/time features (上がり3F・走破時計・着差・通過順位・脚質).

All result-time signals are aggregated over the horse's PAST races only (race_date < R, same-day
excluded) — the target race's own time/last3f/corner/style never enter its features. Mechanism
mirrors Feature 020: per past race compute an IN-RACE RELATIVE value (vs that race's finisher mean,
which naturally absorbs distance/surface/going since every runner shared them), then take the
horse's recent-N rolling aggregate and merge_asof(backward, allow_exact_matches=False) onto R.

Groups:
- pace_time (MVP): rel_last3f_avg/best, rel_time_avg, finish_diff_avg/best.
- position_style (optional, ablation-gated): rel_corner_pos_avg, front_runner_rate, closer_rate.

Leak boundary (codex R2): the in-race relative baseline for a PAST race is built from that past
race's finishers only — never from R, R's rivals, same-day, or future races. Lower is better for
上がり/時計/着差/通過 (so "best" = rolling min).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames

PACE_TIME_COLUMNS = [
    "rel_last3f_avg", "rel_last3f_best", "rel_time_avg",
    "finish_diff_avg", "finish_diff_best",
]
POSITION_STYLE_COLUMNS = ["rel_corner_pos_avg", "front_runner_rate", "closer_rate"]
#: Feature 056: テン3F (first 3F) — the front-half pace axis 023 lacked. Same in-race-relative +
#: recent-N + strictly-before machinery; "best" = min = fastest early pace. pace_balance per past
#: run = rel_last3f − rel_first3f (positive = front-loaded 前傾, negative = closer-shaped 後傾).
PACE_FIRST3F_COLUMNS = [
    "asof_rel_first3f_avg", "asof_rel_first3f_best", "asof_pace_balance_avg",
]
PACE_COLUMNS = [*PACE_TIME_COLUMNS, *POSITION_STYLE_COLUMNS, *PACE_FIRST3F_COLUMNS]

_RECENT_N = 5
_FRONT_STYLES = {"逃げ", "先行"}
_CLOSER_STYLES = {"差し", "追込", "ﾏｸﾘ", "マクリ"}


def _to_seconds(s: pd.Series) -> pd.Series:
    return pd.to_timedelta(s, errors="coerce").dt.total_seconds()


def _final_corner(orders) -> float:
    if isinstance(orders, (list, tuple)) and len(orders) > 0:
        try:
            return float(orders[-1])
        except (TypeError, ValueError):
            return np.nan
    return np.nan


def _pace_runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status", "running_style"]].copy()
    rr_cols = ["race_id", "horse_id", "finish_order", "result_status",
               "last_3f", "finish_time", "finish_time_diff", "corner_orders"]
    # Feature 056: first_3f is optional so pre-055 Frames fixtures keep working (all-NaN then)
    has_first3f = "first_3f" in frames.race_results.columns
    if has_first3f:
        rr_cols.append("first_3f")
    rr = frames.race_results[rr_cols].copy()
    runs = rh.merge(races, on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    runs["is_finished"] = (runs["result_status"] == ResultStatus.FINISHED).astype(int)
    runs["last3f_s"] = pd.to_numeric(runs["last_3f"], errors="coerce")
    runs["first3f_s"] = (
        pd.to_numeric(runs["first_3f"], errors="coerce") if has_first3f
        else pd.Series(np.nan, index=runs.index)
    )
    runs["time_s"] = _to_seconds(runs["finish_time"])
    runs["diff_s"] = _to_seconds(runs["finish_time_diff"])
    runs["corner_last"] = runs["corner_orders"].map(_final_corner)
    # field size = started horses per race (for position normalization)
    fs = runs.groupby("race_id", as_index=False)["is_started"].sum().rename(
        columns={"is_started": "field_size"}
    )
    runs = runs.merge(fs, on="race_id", how="left")

    # IN-RACE RELATIVE baselines from FINISHERS of THAT race only (absorbs distance/surface/going).
    fin = runs[runs["is_finished"] == 1]
    means = fin.groupby("race_id", as_index=False).agg(
        race_mean_last3f=("last3f_s", "mean"),
        race_mean_time=("time_s", "mean"),
        race_mean_first3f=("first3f_s", "mean"),  # Feature 056 (NaN race-wide pre-backfill)
    )
    runs = runs.merge(means, on="race_id", how="left")
    runs["rel_last3f"] = runs["last3f_s"] - runs["race_mean_last3f"]
    runs["rel_time"] = runs["time_s"] - runs["race_mean_time"]
    runs["rel_first3f"] = runs["first3f_s"] - runs["race_mean_first3f"]
    # 056: per-run pace balance — positive = front-loaded (前傾), NaN if either 3F missing
    runs["pace_balance"] = runs["rel_last3f"] - runs["rel_first3f"]
    runs["rel_corner"] = np.where(
        runs["field_size"] > 0, runs["corner_last"] / runs["field_size"], np.nan
    )
    runs["is_front"] = runs["running_style"].isin(_FRONT_STYLES).astype(float)
    runs["is_closer"] = runs["running_style"].isin(_CLOSER_STYLES).astype(float)
    return runs


def _rolling_asof(
    src: pd.DataFrame, targets: pd.DataFrame, specs: dict[str, tuple[str, str]]
) -> pd.DataFrame:
    """For each (col -> (base, agg)) build a recent-N rolling agg per horse, then as-of merge to R.

    ``src`` rows must be the eligible past appearances (already filtered). agg in {mean,min}.
    """
    src = src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = src.groupby("horse_id", sort=False)
    out_cols = ["horse_id", "race_date"]
    for col, (base, agg) in specs.items():
        roll = g[base].rolling(_RECENT_N, min_periods=1)
        series = roll.mean() if agg == "mean" else roll.min()
        # rolling returns a MultiIndex (horse_id, orig_index); align back by original position
        src[col] = series.reset_index(level=0, drop=True)
        out_cols.append(col)
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(
        t, src[out_cols].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )


def build_pace_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-023 pace/time features. All as-of race_date < R."""
    runs = _pace_runs(frames)
    targets = runs[["race_id", "horse_id", "race_date"]].copy()

    # pace_time + corner: aggregate over FINISHED past races (where time/last3f/corner exist).
    fin = runs[runs["is_finished"] == 1]
    fin_feat = _rolling_asof(
        fin, targets,
        {
            "rel_last3f_avg": ("rel_last3f", "mean"),
            "rel_last3f_best": ("rel_last3f", "min"),
            "rel_time_avg": ("rel_time", "mean"),
            "finish_diff_avg": ("diff_s", "mean"),
            "finish_diff_best": ("diff_s", "min"),
            "rel_corner_pos_avg": ("rel_corner", "mean"),
            # Feature 056: テン3F — min = fastest relative early pace
            "asof_rel_first3f_avg": ("rel_first3f", "mean"),
            "asof_rel_first3f_best": ("rel_first3f", "min"),
            "asof_pace_balance_avg": ("pace_balance", "mean"),
        },
    )
    # style: aggregate over STARTED past races (running_style is an entry attribute).
    started = runs[runs["is_started"] == 1]
    sty_feat = _rolling_asof(
        started, targets,
        {"front_runner_rate": ("is_front", "mean"), "closer_rate": ("is_closer", "mean")},
    )

    out = (
        targets[["race_id", "horse_id"]]
        .merge(fin_feat[["race_id", "horse_id", *PACE_TIME_COLUMNS, "rel_corner_pos_avg",
                         *PACE_FIRST3F_COLUMNS]],
               on=["race_id", "horse_id"], how="left")
        .merge(sty_feat[["race_id", "horse_id", "front_runner_rate", "closer_rate"]],
               on=["race_id", "horse_id"], how="left")
    )
    return out[["race_id", "horse_id", *PACE_COLUMNS]]
