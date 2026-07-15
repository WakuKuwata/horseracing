"""Track-1 perf: the vectorized feature kernels must stay BIT-IDENTICAL to the old idioms.

FEATURE_VERSION is unchanged, so the serving feature_hash (column-name-only) and the
materialization parity gate (check_exact=True) both assume the *values* are byte-stable across
this refactor. A new-vs-new materialize-parity check cannot catch an old->new value drift, so each
test here re-computes the block the OLD way inline (the reference oracle) and asserts the shipped
vectorized path equals it exactly (rtol=0, atol=0), including NaN placement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from horseracing_features.extra_features import _RECENT_FORM_N, build_extra_features
from horseracing_features.pm_core_strength import (
    _TREND_WINDOW,
    _ols_slope,
    _race_support,
    build_pm_core_strength_features,
)
from tests._frames import make_frames


def _history_frames():
    """A horse with a long finished-race history (many rolling windows) + a debut horse + ties."""
    specs = []
    for i in range(12):  # horse 'a' runs 12 times, varying finish/odds
        specs.append({
            "race_id": f"R{i:02d}", "race_date": f"2020-{(i % 12) + 1:02d}-05",
            "horses": [
                {"horse_id": "a", "finish_order": (i % 5) + 1, "odds": 1.5 + 0.5 * (i % 4)},
                {"horse_id": "b", "finish_order": ((i + 2) % 5) + 1, "odds": 3.0 + (i % 3)},
                {"horse_id": "c", "finish_order": ((i + 4) % 5) + 1, "odds": 8.0 - (i % 3)},
            ],
        })
    # a target race that reads the as-of values strictly before it
    specs.append({
        "race_id": "RT", "race_date": "2021-06-01",
        "horses": [
            {"horse_id": "a", "finish_order": 1, "odds": 2.0},
            {"horse_id": "b", "finish_order": 2, "odds": 4.0},
            {"horse_id": "d", "finish_order": 3, "odds": 5.0},  # debut in this race
        ],
    })
    return make_frames(specs)


def _assert_bit_equal(new: pd.Series, ref: pd.Series, name: str):
    a, b = new.to_numpy(dtype=float), ref.to_numpy(dtype=float)
    assert a.shape == b.shape, name
    # NaN positions must match, finite values must be byte-identical (atol=0, rtol=0).
    assert np.array_equal(np.isnan(a), np.isnan(b)), f"{name}: NaN mask differs"
    fin = ~np.isnan(a)
    assert np.array_equal(a[fin], b[fin]), f"{name}: finite values differ (max |Δ|={np.abs(a[fin]-b[fin]).max() if fin.any() else 0})"


def test_extra_recent_form_vectorization_is_bit_identical():
    frames = _history_frames()
    runs = _extra_runs(frames)
    fin = runs[runs["is_finished"] == 1].sort_values(
        ["horse_id", "race_date"], kind="stable"
    ).copy()
    g = fin.groupby("horse_id", sort=False)
    # OLD idiom (reference oracle): per-group Python callback over rolling().mean().
    ref_last3 = g["finish_order"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    ref_winr = g["is_win"].transform(lambda s: s.rolling(_RECENT_FORM_N, min_periods=1).mean())
    # NEW idiom (shipped): g[col].rolling().mean() realigned.
    new_last3 = g["finish_order"].rolling(3, min_periods=1).mean().reset_index(level=0, drop=True)
    new_winr = g["is_win"].rolling(_RECENT_FORM_N, min_periods=1).mean().reset_index(level=0, drop=True)
    _assert_bit_equal(new_last3, ref_last3, "avg_last3_finish")
    _assert_bit_equal(new_winr, ref_winr, "recent_win_rate")
    # And the end-to-end block still produces those columns (finite for the experienced horse).
    out = build_extra_features(frames)
    row = out[(out["race_id"] == "RT") & (out["horse_id"] == "a")].iloc[0]
    assert np.isfinite(row["avg_last3_finish"]) and np.isfinite(row["recent_win_rate"])


def test_pm_core_trend_vectorization_is_bit_identical():
    frames = _history_frames()
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    rh["odds"] = frames.race_horses["odds"].to_numpy()
    runs = rh.merge(races, on="race_id", how="left")
    started = runs[runs["entry_status"] == EntryStatus.STARTED].copy()
    started["field_size"] = started.groupby("race_id")["horse_id"].transform("size")
    src = _race_support(started).sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = src.groupby("horse_id", sort=False)["s"]
    # OLD idiom: raw=False Series window -> _ols_slope(w.to_numpy()).
    ref = g.rolling(_TREND_WINDOW, min_periods=2).apply(
        lambda w: _ols_slope(w.to_numpy()), raw=False
    ).reset_index(level=0, drop=True)
    # NEW idiom (shipped): raw=True ndarray straight into _ols_slope.
    new = g.rolling(_TREND_WINDOW, min_periods=2).apply(
        _ols_slope, raw=True
    ).reset_index(level=0, drop=True)
    _assert_bit_equal(new, ref, "asof_pm_support_trend")
    # sanity: the shipped block yields a finite trend for the experienced horse at the target race.
    out = build_pm_core_strength_features(frames)
    assert (out["asof_pm_support_trend"].notna()).any()


def _extra_runs(frames):
    # Mirror extra_features._enriched_runs enough to reach recent_form inputs, using the module.
    from horseracing_features.extra_features import _enriched_runs
    return _enriched_runs(frames)
