"""Feature 063 (spike): closing-speed figure — absolute last-3F ability vs a course baseline.

The sectional sibling of 061's finish-time speed figure. 023 has rel_last3f (last-3F vs that
race's finisher mean = pace-clean but member-relative); 061 proved the ABSOLUTE version of a
sibling metric (finish TIME vs course baseline) adds value ON TOP of its relative counterpart
(rel_time_avg). This tests the same for the closing sectional: absolute last-3F vs the
(venue × track × exact-distance × going) cell baseline, aggregated per horse as-of.

Mechanics are 061's exactly (daily cumsum−same-day cell baseline, per-run z clipped ±5, as-of
horse aggregation) with the metric swapped finish_time → last_3f (already seconds). Higher z =
faster closing than the cell-typical. Pace-contaminated more than finish time (a slow-paced race
lets everyone close fast), which is precisely why this is spike-first: if the absolute closing
axis is either redundant with {rel_last3f, 061 speed figure} or too pace-noisy, the gate says so.

Leak/materialize identical to 061: strictly-before, same-day excluded, pool-end independent, no
new source columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import ResultStatus

from .loader import Frames

CLOSING_FIGURE_COLUMNS = [
    "asof_closefig_avg",
    "asof_closefig_best",
    "asof_closefig_recent3",
    "asof_closefig_last",
    "asof_closefig_count",
]

_CELL = ["venue_code", "track_type", "distance", "going"]
MIN_RACES = 50
Z_CLIP = 5.0


def _race_samples(frames: Frames) -> pd.DataFrame:
    """One row per race: race_id, race_date, cell, finisher-mean last_3f (seconds)."""
    races = frames.races[["race_id", "race_date", *_CELL]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rr = frames.race_results[["race_id", "result_status", "last_3f"]].copy()
    rr["l3f"] = pd.to_numeric(rr["last_3f"], errors="coerce")
    fin = rr[(rr["result_status"] == ResultStatus.FINISHED) & rr["l3f"].notna()]
    means = fin.groupby("race_id", as_index=False).agg(sample_l3f=("l3f", "mean"))
    out = races.merge(means, on="race_id", how="inner")
    return out.dropna(subset=[*_CELL, "sample_l3f"])


def _cell_baseline_before(samples: pd.DataFrame) -> pd.DataFrame:
    daily = (
        samples.assign(x=samples["sample_l3f"], x2=samples["sample_l3f"] ** 2)
        .groupby([*_CELL, "race_date"], as_index=False)
        .agg(n=("x", "size"), s=("x", "sum"), ss=("x2", "sum"))
        .sort_values([*_CELL, "race_date"], kind="stable")
    )
    g = daily.groupby(_CELL, sort=False)
    n_b = g["n"].cumsum() - daily["n"]
    s_b = g["s"].cumsum() - daily["s"]
    ss_b = g["ss"].cumsum() - daily["ss"]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s_b / n_b
        var = ss_b / n_b - mean**2
        std = np.sqrt(np.clip(var, 0.0, None))
    valid = (n_b >= MIN_RACES) & (std > 0)
    daily["cell_mean_before"] = np.where(valid, mean, np.nan)
    daily["cell_std_before"] = np.where(valid, std, np.nan)
    return daily[[*_CELL, "race_date", "cell_mean_before", "cell_std_before"]]


def _figure_runs(frames: Frames) -> pd.DataFrame:
    samples = _race_samples(frames)
    baseline = _cell_baseline_before(samples)
    races = frames.races[["race_id", "race_date", *_CELL]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rr = frames.race_results[["race_id", "horse_id", "result_status", "last_3f"]].copy()
    rr["l3f"] = pd.to_numeric(rr["last_3f"], errors="coerce")
    runs = rr[(rr["result_status"] == ResultStatus.FINISHED) & rr["l3f"].notna()].merge(
        races, on="race_id", how="left"
    )
    runs = runs.merge(baseline, on=[*_CELL, "race_date"], how="left")
    # faster closing = SMALLER last_3f -> positive figure when below the cell mean
    z = (runs["cell_mean_before"] - runs["l3f"]) / runs["cell_std_before"]
    runs["closefig_z"] = np.clip(z, -Z_CLIP, Z_CLIP)
    return runs[runs["closefig_z"].notna()][["horse_id", "race_date", "closefig_z"]]


def build_closing_figure_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) closing_figure columns. All as-of race_date < R (same-day off)."""
    rh = frames.race_horses[["race_id", "horse_id"]].copy()
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    targets = rh.merge(races, on="race_id", how="left")

    src = _figure_runs(frames).sort_values(["horse_id", "race_date"], kind="stable")
    if src.empty:
        out = targets[["race_id", "horse_id"]].copy()
        for c in CLOSING_FIGURE_COLUMNS:
            out[c] = np.nan
        out["asof_closefig_count"] = 0.0
        return out[["race_id", "horse_id", *CLOSING_FIGURE_COLUMNS]]

    g = src.groupby("horse_id", sort=False)["closefig_z"]
    src["asof_closefig_avg"] = g.expanding(min_periods=1).mean().reset_index(level=0, drop=True)
    src["asof_closefig_best"] = g.cummax()
    src["asof_closefig_recent3"] = (
        g.rolling(3, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    src["asof_closefig_last"] = src["closefig_z"]
    src["asof_closefig_count"] = (
        g.expanding(min_periods=1).count().reset_index(level=0, drop=True)
    )

    merged = pd.merge_asof(
        targets.sort_values("race_date", kind="stable"),
        src[["horse_id", "race_date", *CLOSING_FIGURE_COLUMNS]].sort_values(
            "race_date", kind="stable"
        ),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    merged["asof_closefig_count"] = merged["asof_closefig_count"].fillna(0.0)
    for c in CLOSING_FIGURE_COLUMNS:
        merged[c] = merged[c].astype("float64")
    return merged[["race_id", "horse_id", *CLOSING_FIGURE_COLUMNS]]
