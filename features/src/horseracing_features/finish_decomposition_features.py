"""Feature 088: finish-rank decomposition — field-size normalization + lag/window decomposition.

``prev_finish`` (the raw previous finishing position) dominates gain importance, but a raw position
is field-size dependent (5th of 18 vs 5th of 8 are the same number) and the series between "last
start" and "mean of the last 3" is not decomposed at all. This bundle adds the normalized position
plus the individual lags / longer windows / trend so the axis can be MEASURED and closed.

Normalized position (FR-001, frozen): ``finish_pct = (finish_order − 1) / (n_started − 1)``.
The estimand is FIELD-SIZE normalization, so the denominator is the STARTED field, not the
finishers — the value means "the fraction of the started field that finished ahead of me"
(0 = win). With non-finishers in the race the maximum is below 1: "1 = last" is deliberately NOT
guaranteed (codex review, 論点A). ``n_started == 1`` → NaN; an out-of-range ``finish_order``
(< 1 or > n_started) is a data error → NaN + surfaced by the coverage audit (INV-C2a).

Series population (INV-C4): FINISHED runs only — the same rule the existing ``prev_finish``
(finished-only merge_asof, history.py) and ``avg_last3_finish`` (finished-only rolling) use, so
``prev2_finish`` means exactly "the one before ``prev_finish``".

Leak boundary (constitution II): every column aggregates strictly-before runs only —
merge_asof(backward, allow_exact_matches=False) = strictly before the target day, same day
excluded. The target race's own result never enters its own features.

NaN policy (INV-C6, 憲法 IV): missing observations and any NaN inside a normalized window
propagate (never 0-filled — Unknown ≠ 0). ``min_periods`` is fixed per column so a window's value
always means "that many runs", never "as many as happened to exist".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames

FINISH_DECOMP_COLUMNS = [
    "prev_finish_pct",
    "prev2_finish",
    "prev3_finish",
    "prev2_finish_pct",
    "prev3_finish_pct",
    "avg_last3_finish_pct",
    "avg_last5_finish",
    "avg_last5_finish_pct",
    "best_finish_pct",
    "finish_trend5",
]

# frozen window params (specs/088 contracts/feature-columns.md — do NOT tune post-OOS)
_AVG3_WINDOW = 3
_AVG5_WINDOW = 5
_TREND_WINDOW = 5


def _ols_slope(y: np.ndarray) -> float:
    """OLS slope of y over evenly-spaced x = 0..k-1 (oldest→newest). Degenerate → NaN.

    NOTE (INV-C9): the window is 5, not 3, on purpose — a 3-point evenly-spaced OLS slope equals
    (endpoint difference)/2, i.e. a linear combination of lags this bundle already carries, so it
    would add no independent information. Do NOT add lag-4/lag-5 columns to the bundle: that would
    make this slope a linear combination of recorded columns too.
    """
    k = len(y)
    if k < 2:
        return np.nan
    x = np.arange(k, dtype=float)
    xm = x.mean()
    denom = ((x - xm) ** 2).sum()
    if denom == 0:
        return np.nan
    return float(((x - xm) * (y - y.mean())).sum() / denom)


def _race_started_size(frames: Frames) -> pd.DataFrame:
    """Race-level primitive: per race_id the STARTED field size (Feature 072 — always computed
    over the FULL pool, never restricted to the target horses, or the denominator would change)."""
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    started = rh[rh["entry_status"] == EntryStatus.STARTED]
    return (
        started.groupby("race_id", as_index=False)["horse_id"]
        .size()
        .rename(columns={"size": "n_started"})
    )


def _finished_runs(frames: Frames, n_started: pd.DataFrame) -> pd.DataFrame:
    """Per (race_id, horse_id) FINISHED past runs with race_date, finish_order and finish_pct."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    runs = (
        rr[rr["result_status"] == ResultStatus.FINISHED]
        .merge(races, on="race_id", how="left")
        .merge(n_started, on="race_id", how="left")
    )
    runs = runs.drop(columns=["result_status"])
    order = pd.to_numeric(runs["finish_order"], errors="coerce").astype("float64")
    size = pd.to_numeric(runs["n_started"], errors="coerce").astype("float64")
    # INV-C2 (degenerate denominator) + INV-C2a (range check): both → NaN, never a fabricated value
    valid = order.notna() & size.notna() & (size > 1) & (order >= 1) & (order <= size)
    runs["finish_order"] = order
    runs["finish_pct"] = np.where(valid, (order - 1.0) / (size - 1.0), np.nan)
    return runs[["race_id", "horse_id", "race_date", "finish_order", "finish_pct"]]


def _asof_reductions(src: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Per-horse reductions over the finished series (each source row aggregates up to & including
    itself), then a strictly-before merge onto the targets (same-day excluded)."""
    src = src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g_order = src.groupby("horse_id", sort=False)["finish_order"]
    g_pct = src.groupby("horse_id", sort=False)["finish_pct"]

    # the merge picks the last source row strictly before the target, so that row IS "prev";
    # shift(1)/shift(2) within the horse are therefore prev2 / prev3.
    src["prev_finish_pct"] = src["finish_pct"]
    src["prev2_finish"] = g_order.shift(1)
    src["prev3_finish"] = g_order.shift(2)
    src["prev2_finish_pct"] = g_pct.shift(1)
    src["prev3_finish_pct"] = g_pct.shift(2)

    # rolling means: min_periods == window, and pandas counts NON-NaN observations, so any NaN
    # inside a normalized window makes the aggregate NaN (INV-C6 propagation, by construction).
    src["avg_last3_finish_pct"] = (
        g_pct.rolling(_AVG3_WINDOW, min_periods=_AVG3_WINDOW)
        .mean()
        .reset_index(level=0, drop=True)
    )
    src["avg_last5_finish"] = (
        g_order.rolling(_AVG5_WINDOW, min_periods=_AVG5_WINDOW)
        .mean()
        .reset_index(level=0, drop=True)
    )
    src["avg_last5_finish_pct"] = (
        g_pct.rolling(_AVG5_WINDOW, min_periods=_AVG5_WINDOW)
        .mean()
        .reset_index(level=0, drop=True)
    )
    # expanding min skips NaN and is NaN only while no valid pct has been seen at all
    src["best_finish_pct"] = g_pct.expanding().min().reset_index(level=0, drop=True)
    src["finish_trend5"] = (
        g_pct.rolling(_TREND_WINDOW, min_periods=_TREND_WINDOW)
        .apply(_ols_slope, raw=True)
        .reset_index(level=0, drop=True)
    )

    out_cols = ["horse_id", "race_date", *FINISH_DECOMP_COLUMNS]
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(
        t,
        src[out_cols].sort_values("race_date", kind="stable"),
        on="race_date",
        by="horse_id",
        direction="backward",
        allow_exact_matches=False,  # strictly before + same day excluded
    )


def build_finish_decomposition_features(
    frames: Frames, *, target_race_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Per (race_id, horse_id) finish-decomposition features (all aggregate race_date < R).

    Feature 072 projection (per-horse shape, INV-C10): the race-level primitive ``n_started``
    stays computed over the FULL pool — it is the normalization denominator and must not depend on
    which races are being served. Only the per-horse reduction SOURCE and the emitted target rows
    are restricted, and the per-horse rolling/expanding reductions read that horse's own past rows
    only, so the projected rows are byte-identical to the full build's."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    targets = (
        frames.race_horses[["race_id", "horse_id"]]
        .merge(races, on="race_id", how="left")
    )

    n_started = _race_started_size(frames)  # full pool — never projected
    runs = _finished_runs(frames, n_started)

    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)]
        horses = frozenset(targets["horse_id"])
        runs = runs[runs["horse_id"].isin(horses)]

    if runs.empty:
        out = targets[["race_id", "horse_id"]].copy()
        for c in FINISH_DECOMP_COLUMNS:
            out[c] = np.nan
        out[FINISH_DECOMP_COLUMNS] = out[FINISH_DECOMP_COLUMNS].astype("float64")
        return out[["race_id", "horse_id", *FINISH_DECOMP_COLUMNS]].reset_index(drop=True)

    feat = _asof_reductions(runs, targets)
    out = targets[["race_id", "horse_id"]].merge(
        feat[["race_id", "horse_id", *FINISH_DECOMP_COLUMNS]],
        on=["race_id", "horse_id"],
        how="left",
    )
    # pin float64 so a pool without any qualifying history keeps the same dtypes as a full build
    out[FINISH_DECOMP_COLUMNS] = out[FINISH_DECOMP_COLUMNS].astype("float64")
    return out[["race_id", "horse_id", *FINISH_DECOMP_COLUMNS]].reset_index(drop=True)
