"""Feature 070 (F03): robust past-market RANK-percentile as-of features (accuracy-first model).

058 uses the raw popularity rank (confounds "5th of 16" with "5th of 8"); F03 uses the
field-size-normalised rank PERCENTILE ``u = 1 - (rank-1)/(N_started-1)`` (u=1 top favourite,
u=0 least favoured, N=1 -> u=1). This is the odds-provenance-insensitive robust fallback and the
recipe-level REPLACEMENT candidate for 058's raw rank (candidate drops the 058 ``past_market``
group; baseline keeps it — never adopted together, FR-002/attribution).

Popularity-only complete-field (analyze U1 / codex 見落とし): a past race contributes iff EVERY
started horse carries a valid popularity — F03 does NOT require odds completeness (that is F02's
gate), so it survives odds-provenance gaps. ``rank`` is the COMPETITION rank (1,2,2,4) of popularity
within the started+valid-popularity set (deterministic; never broken by horse_id/row order). Ties
under competition rank may push >3 horses to rank<=3 (top3fav counts them all).

Leak boundary (constitution II): only PAST starts feed these — merge_asof(backward,
allow_exact_matches=False) = strictly-before + same-day excluded. The target race's own popularity
NEVER enters its features. Column names avoid the odds/popularity leak-guard tokens (``asof_pm_*``).

Frozen params (specs/070 gate-config f03_formula, FR-011/III): min_obs=3 for ALL columns incl.
``rankpct_last`` (a 1-obs percentile is unstable); fav_rank=1; top3_rank_le=3; mean/fav windows=5.

POLICY: MARKET data -> accuracy-first candidate only; the default decision-support model MUST drop
this group (対象レース市場非入力), same as 058/069.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames

PM_RANK_ROBUST_COLUMNS = [
    "asof_pm_rankpct_last",
    "asof_pm_rankpct_mean5",
    "asof_pm_favorite_rate5",
    "asof_pm_top3fav_rate5",
    "asof_pm_rank_obs_count",
]

_MIN_OBS = 3      # NaN below this for ALL F03 columns incl. rankpct_last (analyze A1/A3)
_MEAN_WINDOW = 5
_FAV_WINDOW = 5
_FAV_RANK = 1
_TOP3_RANK_LE = 3


def _valid_popularity(p: pd.Series) -> pd.Series:
    """Valid market popularity: finite positive integer-ish rank (>=1)."""
    p = pd.to_numeric(p, errors="coerce")
    return p.notna() & np.isfinite(p) & (p >= 1)


def rank_percentile_primitive(runs: pd.DataFrame) -> pd.DataFrame:
    """Feature 070 (shared primitive, T007): per (race_id, horse_id, race_date) rank percentile
    ``u`` for COMPLETE-FIELD (popularity-only) past started races. F03 aggregates these; F04
    consumes the SAME ``u`` (adoption != import, FR-004).

    ``runs`` rows are started horses with a ``popularity`` column + ``race_date``. A race
    contributes iff EVERY started horse has valid popularity (complete-field). ``rank`` =
    competition rank of popularity within that set; ``u = 1-(rank-1)/(N-1)`` (N=1 -> u=1).
    Returns columns ``race_id, horse_id, race_date, u, rank, N``.
    """
    r = runs.copy()
    r["_valid"] = _valid_popularity(r["popularity"])
    by_race = r.groupby("race_id")
    valid_count = by_race["_valid"].transform("sum")
    started_count = by_race["_valid"].transform("size")
    r["_complete"] = valid_count == started_count
    q = r[r["_complete"]].copy()
    if q.empty:
        empty = q.assign(u=pd.Series(dtype=float), rank=pd.Series(dtype=float),
                         N=pd.Series(dtype=float))
        return empty[["race_id", "horse_id", "race_date", "u", "rank", "N"]]
    pop = pd.to_numeric(q["popularity"], errors="coerce")
    q["rank"] = pop.groupby(q["race_id"]).rank(method="min")  # competition rank (1,2,2,4)
    q["N"] = q.groupby("race_id")["rank"].transform("size").astype(float)
    denom = q["N"] - 1.0
    q["u"] = np.where(denom > 0, 1.0 - (q["rank"] - 1.0) / denom, 1.0)  # N=1 -> u=1
    return q[["race_id", "horse_id", "race_date", "u", "rank", "N"]]


def _asof(src: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Per-horse as-of reductions of ``u``/rank, then strictly-before merge (058 idiom)."""
    src = src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    src["_fav"] = (src["rank"] == _FAV_RANK).astype(float)
    src["_top3"] = (src["rank"] <= _TOP3_RANK_LE).astype(float)
    g = src.groupby("horse_id", sort=False)
    src["asof_pm_rankpct_last"] = src["u"]
    src["asof_pm_rankpct_mean5"] = (
        g["u"].rolling(_MEAN_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    src["asof_pm_favorite_rate5"] = (
        g["_fav"].rolling(_FAV_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    src["asof_pm_top3fav_rate5"] = (
        g["_top3"].rolling(_FAV_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    src["asof_pm_rank_obs_count"] = (
        g["u"].expanding().count().reset_index(level=0, drop=True)
    )
    out_cols = ["horse_id", "race_date", *PM_RANK_ROBUST_COLUMNS]
    t = targets.sort_values("race_date", kind="stable")
    merged = pd.merge_asof(
        t, src[out_cols].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    return merged


def build_pm_rank_robust_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) F03 as-of features. All aggregate race_date < R (strictly-before)."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    has_pop = "popularity" in frames.race_horses.columns
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    rh["popularity"] = (
        frames.race_horses["popularity"].to_numpy() if has_pop
        else np.full(len(rh), np.nan)
    )
    runs = rh.merge(races, on="race_id", how="left")
    started = runs[runs["entry_status"] == EntryStatus.STARTED].copy()

    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    prim = rank_percentile_primitive(started)
    if prim.empty:
        out = targets[["race_id", "horse_id"]].copy()
        for c in PM_RANK_ROBUST_COLUMNS:
            out[c] = 0.0 if c == "asof_pm_rank_obs_count" else np.nan
        return out[["race_id", "horse_id", *PM_RANK_ROBUST_COLUMNS]]

    feat = _asof(prim, targets)
    out = targets[["race_id", "horse_id"]].merge(
        feat[["race_id", "horse_id", *PM_RANK_ROBUST_COLUMNS]],
        on=["race_id", "horse_id"], how="left",
    )
    # obs_count is a FACT (0 meaningful); below-min-obs continuous cols -> NaN (analyze A1/A3)
    out["asof_pm_rank_obs_count"] = out["asof_pm_rank_obs_count"].fillna(0.0)
    below = out["asof_pm_rank_obs_count"] < _MIN_OBS
    for c in ("asof_pm_rankpct_last", "asof_pm_rankpct_mean5",
              "asof_pm_favorite_rate5", "asof_pm_top3fav_rate5"):
        out.loc[below, c] = np.nan
    return out[["race_id", "horse_id", *PM_RANK_ROBUST_COLUMNS]]
