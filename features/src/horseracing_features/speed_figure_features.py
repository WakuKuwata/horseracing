"""Feature 061: speed figure — absolute time ability vs an as-of course-condition baseline.

The 023 time features are IN-RACE relative (vs that race's finisher mean), so they cannot
compare across member levels. This module adds the missing ABSOLUTE axis: how fast was each
past run against the historical baseline of its (venue × track_type × exact distance × going)
cell, aggregated per horse as-of the target race.

Baseline (leak boundary, INV-F1): one sample per past RACE = that race's finisher-mean time
(race-level sampling so large fields don't dominate the baseline; the min-sample threshold
counts RACES). Cell statistics are strictly-before-DAY via the established daily
"cumsum − same day" mechanism, so a run's own day (including same-day OTHER races of the
cell) never enters its baseline. Cells with fewer than ``MIN_RACES`` prior races (or a
degenerate std) yield NaN — measured coverage on the real DB: 93.2% of races' cells qualify
at the full-period horizon.

Per past run: z = clip((cell_mean_before − time_s) / cell_std_before, ±5) (positive = faster
than baseline). Per-horse as-of aggregation reuses the merge_asof(backward,
allow_exact_matches=False) pattern (strictly-before + same-day excluded). NOTE: the cell mixes
race classes, so the figure is an ABILITY-leaning measure ("how fast vs the class-mixed
course norm"), not a pure condition correction (codex review, research D4).

Columns are pool-end independent (every value depends only on strictly-before data), so the
block is materialize-safe (031/059 precedent). No new source columns are read.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import ResultStatus

from .loader import Frames
from .pace_features import _to_seconds

SPEED_FIGURE_COLUMNS = [
    "asof_spdfig_avg",      # expanding mean of past-run z
    "asof_spdfig_best",     # cummax of past-run z (higher = faster)
    "asof_spdfig_recent3",  # rolling-3 mean of past-run z
    "asof_spdfig_last",     # previous run's z
    "asof_spdfig_count",    # number of valid past figures (reliability; 0.0 = none, a fact)
]

#: baseline cell key — exact distance (JRA distances are discrete) + going (large time effect).
_CELL = ["venue_code", "track_type", "distance", "going"]
#: minimum strictly-before RACE samples for a usable cell baseline (measured: covers 93.2%).
MIN_RACES = 50
#: z clip bounds — breakdowns / tailed-off finishes must not dominate the aggregates.
Z_CLIP = 5.0


def _race_samples(frames: Frames) -> pd.DataFrame:
    """One row per race with a finisher-mean time: race_id, race_date(day), cell, sample_time."""
    races = frames.races[["race_id", "race_date", *_CELL]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rr = frames.race_results[["race_id", "result_status", "finish_time"]].copy()
    rr["time_s"] = _to_seconds(rr["finish_time"])
    fin = rr[(rr["result_status"] == ResultStatus.FINISHED) & rr["time_s"].notna()]
    means = fin.groupby("race_id", as_index=False).agg(sample_time=("time_s", "mean"))
    out = races.merge(means, on="race_id", how="inner")
    # a race with an incomplete cell key cannot contribute to (or receive) a baseline
    return out.dropna(subset=[*_CELL, "sample_time"])


def _cell_baseline_before(samples: pd.DataFrame) -> pd.DataFrame:
    """Strictly-before-DAY expanding mean/std per cell: (cell, race_date) -> mean/std/n.

    Daily aggregate (n, Σx, Σx²) per (cell, day) -> within-cell chronological cumsum minus
    the day's own row = totals over strictly earlier days (the 020 mechanism applied to a
    cross-horse statistic — the whole day is excluded, including same-day other races).
    """
    daily = (
        samples.assign(x=samples["sample_time"], x2=samples["sample_time"] ** 2)
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
    """Per past appearance with a valid z: horse_id, race_date, z."""
    samples = _race_samples(frames)
    baseline = _cell_baseline_before(samples)

    races = frames.races[["race_id", "race_date", *_CELL]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rr = frames.race_results[["race_id", "horse_id", "result_status", "finish_time"]].copy()
    rr["time_s"] = _to_seconds(rr["finish_time"])
    runs = rr[(rr["result_status"] == ResultStatus.FINISHED) & rr["time_s"].notna()].merge(
        races, on="race_id", how="left"
    )
    runs = runs.merge(baseline, on=[*_CELL, "race_date"], how="left")
    z = (runs["cell_mean_before"] - runs["time_s"]) / runs["cell_std_before"]
    runs["spdfig_z"] = np.clip(z, -Z_CLIP, Z_CLIP)
    return runs[runs["spdfig_z"].notna()][["horse_id", "race_date", "spdfig_z"]]


def build_speed_figure_features(
    frames: Frames, *, target_race_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Per (race_id, horse_id) speed_figure columns. All as-of race_date < R (same-day excluded).

    Feature 072: the (venue×track×dist×going) cell BASELINE in ``_figure_runs`` stays computed over
    the FULL frame; only the per-horse expanding/rolling source and the target rows are restricted
    to the target races' horses — byte-identical on those rows (INV-P1)."""
    rh = frames.race_horses[["race_id", "horse_id"]].copy()
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    targets = rh.merge(races, on="race_id", how="left")

    src = _figure_runs(frames).sort_values(["horse_id", "race_date"], kind="stable")
    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)]
        src = src[src["horse_id"].isin(frozenset(targets["horse_id"]))]
    if src.empty:
        out = targets[["race_id", "horse_id"]].copy()
        for c in SPEED_FIGURE_COLUMNS:
            out[c] = np.nan
        out["asof_spdfig_count"] = 0.0
        return out[["race_id", "horse_id", *SPEED_FIGURE_COLUMNS]]

    g = src.groupby("horse_id", sort=False)["spdfig_z"]
    src["asof_spdfig_avg"] = g.expanding(min_periods=1).mean().reset_index(level=0, drop=True)
    src["asof_spdfig_best"] = g.cummax()
    src["asof_spdfig_recent3"] = (
        g.rolling(3, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    src["asof_spdfig_last"] = src["spdfig_z"]
    src["asof_spdfig_count"] = g.expanding(min_periods=1).count().reset_index(level=0, drop=True)

    merged = pd.merge_asof(
        targets.sort_values("race_date", kind="stable"),
        src[["horse_id", "race_date", *SPEED_FIGURE_COLUMNS]].sort_values(
            "race_date", kind="stable"
        ),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    # count is a FACT (number of valid past figures): no prior run -> 0.0, never NaN (INV-F4).
    merged["asof_spdfig_count"] = merged["asof_spdfig_count"].fillna(0.0)
    for c in SPEED_FIGURE_COLUMNS:
        merged[c] = merged[c].astype("float64")
    return merged[["race_id", "horse_id", *SPEED_FIGURE_COLUMNS]]
