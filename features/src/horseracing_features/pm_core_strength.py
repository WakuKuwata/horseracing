"""Feature 069 (F02): past-race market SUPPORT as-of features (accuracy-first model only).

058 uses past popularity RANK; F02 uses the odds QUANTITY. At each PAST start we recover the
crowd's market vote-share ``q_i = (1/O_i) / Σ_j(1/O_j)`` and the field-size-normalized SUPPORT
``s_i = log(q_i · N)`` (s=0 uniform support, >0 above-uniform, <0 below). This distinguishes the
"same 1番人気 at 25% vs 60%" that a rank cannot.

Complete-field rule (FR-006, codex D3): ``q`` is computed ONLY for races where EVERY started
horse has a valid odds (``1.0 ≤ O < 999.9``; 1.0 is a legit 元返し favorite and is KEPT — dropping
it would void the strong-favorite races). One invalid odds voids the whole race's ``s`` — never
renormalize a partial field. Races with no valid ``s`` history yield NaN + obs_count 0 + has_obs 0.

Leak boundary (constitution II): only PAST starts feed these — merge_asof(backward,
allow_exact_matches=False) = strictly-before + same-day excluded. The target race's own odds NEVER
enter its features. Column names avoid the odds/popularity leak-guard tokens (``asof_pm_*``,
041/058 precedent).

Formula params (frozen in specs/069 gate-config, FR-011): recent-K = recent K valid market
observations; shrunk mean = Σ recent-K(s) / (n + λ) with neutral prior 0 (λ mean3/mean5=2,
career=5); trend = OLS slope of the last 3 obs (NaN if <2); sd5 = sample std ddof=1 of last 5
(NaN if <2); best5 = max of last 5; N=1 races give s=0 and ARE counted in obs_count.

POLICY: MARKET data → accuracy-first candidate model only; the default decision-support model MUST
drop this group (keep p⊥q), same as 058.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames

PM_CORE_STRENGTH_COLUMNS = [
    "asof_pm_support_last",
    "asof_pm_support_mean3",
    "asof_pm_support_mean5",
    "asof_pm_support_best5",
    "asof_pm_support_career",
    "asof_pm_support_trend",
    "asof_pm_support_sd5",
    "asof_pm_obs_count",
    "asof_pm_has_obs",
]

# frozen formula params (mirror specs/069 gate-config f02_formula, FR-011)
_LAMBDA_MEAN = 2.0
_LAMBDA_CAREER = 5.0
_TREND_WINDOW = 3
_SD_WINDOW = 5
_BEST_WINDOW = 5
_ODDS_MIN = 1.0        # 元返し floor, KEPT valid (analyze D1)
_ODDS_CAP = 999.9      # netkeiba cap, treated invalid (cap_pending_confirm)


def _valid_odds(o: pd.Series) -> pd.Series:
    """Valid market odds: finite and 1.0 ≤ O < 999.9 (data-error sentinels ≤0/non-finite out)."""
    o = pd.to_numeric(o, errors="coerce")
    return o.notna() & np.isfinite(o) & (o >= _ODDS_MIN) & (o < _ODDS_CAP)


def _race_support(runs: pd.DataFrame) -> pd.DataFrame:
    """Per (race_id, horse_id) market support ``s`` for COMPLETE-FIELD past started races only.

    ``runs`` rows are started horses with an ``odds`` column and ``field_size`` (started count).
    A race contributes ``s`` for all its horses iff EVERY started horse has valid odds; otherwise
    the race is dropped entirely (complete-field, FR-006). Returns rows with columns
    ``race_id, horse_id, race_date, s``.
    """
    r = runs.copy()
    r["_valid"] = _valid_odds(r["odds"])
    # complete-field: a race qualifies iff all its started horses have valid odds
    by_race = r.groupby("race_id")
    valid_count = by_race["_valid"].transform("sum")
    started_count = by_race["_valid"].transform("size")
    r["_complete"] = valid_count == started_count
    q = r[r["_complete"]].copy()
    if q.empty:
        return q.assign(s=pd.Series(dtype=float))[["race_id", "horse_id", "race_date", "s"]]
    inv = 1.0 / pd.to_numeric(q["odds"], errors="coerce")
    denom = q.groupby("race_id")["odds"].transform(lambda o: (1.0 / pd.to_numeric(o)).sum())
    q["q"] = inv / denom
    n = q.groupby("race_id")["odds"].transform("size")
    q["s"] = np.log(q["q"] * n)  # N=1 -> q=1, s=log(1*1)=0 (counted)
    return q[["race_id", "horse_id", "race_date", "s"]]


def _ols_slope(y: np.ndarray) -> float:
    """OLS slope of y over evenly-spaced index 0..k-1 (time order). <2 points -> NaN."""
    k = len(y)
    if k < 2:
        return np.nan
    x = np.arange(k, dtype=float)
    xm = x.mean()
    denom = ((x - xm) ** 2).sum()
    if denom == 0:
        return np.nan
    return float(((x - xm) * (y - y.mean())).sum() / denom)


def _asof_reductions(src: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Per-horse as-of reductions of ``s`` (each source row aggregates obs up to & incl. itself),
    then strictly-before merge (allow_exact_matches=False excludes same-day), 058 idiom."""
    src = src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = src.groupby("horse_id", sort=False)["s"]
    src["asof_pm_support_last"] = src["s"]
    cnt = g.expanding().count().reset_index(level=0, drop=True)
    src["asof_pm_obs_count"] = cnt
    src["asof_pm_has_obs"] = 1.0
    # shrunk means: Σ recent-K(s) / (n_recent + λ), neutral prior 0
    for col, k, lam in (("asof_pm_support_mean3", 3, _LAMBDA_MEAN),
                        ("asof_pm_support_mean5", 5, _LAMBDA_MEAN)):
        rsum = g.rolling(k, min_periods=1).sum().reset_index(level=0, drop=True)
        rcnt = g.rolling(k, min_periods=1).count().reset_index(level=0, drop=True)
        src[col] = rsum / (rcnt + lam)
    csum = g.expanding().sum().reset_index(level=0, drop=True)
    src["asof_pm_support_career"] = csum / (cnt + _LAMBDA_CAREER)
    src["asof_pm_support_best5"] = (
        g.rolling(_BEST_WINDOW, min_periods=1).max().reset_index(level=0, drop=True)
    )
    src["asof_pm_support_sd5"] = (
        g.rolling(_SD_WINDOW, min_periods=2).std(ddof=1).reset_index(level=0, drop=True)
    )
    src["asof_pm_support_trend"] = (
        g.rolling(_TREND_WINDOW, min_periods=2)
        .apply(lambda w: _ols_slope(w.to_numpy()), raw=False)
        .reset_index(level=0, drop=True)
    )
    out_cols = ["horse_id", "race_date", *PM_CORE_STRENGTH_COLUMNS]
    t = targets.sort_values("race_date", kind="stable")
    merged = pd.merge_asof(
        t, src[out_cols].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    # targets with no strictly-before valid obs: count/has_obs are FACTS -> 0, continuous -> NaN
    merged["asof_pm_obs_count"] = merged["asof_pm_obs_count"].fillna(0.0)
    merged["asof_pm_has_obs"] = merged["asof_pm_has_obs"].fillna(0.0)
    return merged


def build_pm_core_strength_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) F02 as-of features. All aggregate race_date < R (strictly-before)."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    has_odds = "odds" in frames.race_horses.columns
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    if has_odds:
        rh["odds"] = frames.race_horses["odds"].to_numpy()
    else:
        rh["odds"] = np.nan
    runs = rh.merge(races, on="race_id", how="left")
    started = runs[runs["entry_status"] == EntryStatus.STARTED].copy()
    started["field_size"] = started.groupby("race_id")["horse_id"].transform("size")

    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    support = _race_support(started)  # complete-field s per past started horse
    if support.empty:
        out = targets[["race_id", "horse_id"]].copy()
        for c in PM_CORE_STRENGTH_COLUMNS:
            out[c] = 0.0 if c in ("asof_pm_obs_count", "asof_pm_has_obs") else np.nan
        return out[["race_id", "horse_id", *PM_CORE_STRENGTH_COLUMNS]]

    feat = _asof_reductions(support, targets)
    out = targets[["race_id", "horse_id"]].merge(
        feat[["race_id", "horse_id", *PM_CORE_STRENGTH_COLUMNS]],
        on=["race_id", "horse_id"], how="left",
    )
    out["asof_pm_obs_count"] = out["asof_pm_obs_count"].fillna(0.0)
    out["asof_pm_has_obs"] = out["asof_pm_has_obs"].fillna(0.0)
    return out[["race_id", "horse_id", *PM_CORE_STRENGTH_COLUMNS]]
