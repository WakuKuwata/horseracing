"""Feature 070 (F04): past-market EXPECTATION-RESIDUAL as-of features (accuracy-first model).

"How wrong was the market about this horse, in the past?" Two SIGN-CARRYING residuals over two
DISTINCT populations (never mixed):

- finish_residual  e = v - u   (FINISHED past starts): v = finish-order percentile
  ``1-(finish_order-1)/(N_started-1)`` (same N_started denominator as u, so v and u share scale,
  analyze U1); u = F03 rank percentile. e>0 => finished better than the market ranked it.
- win_residual     w = I(win) - q   (STARTED past starts, non-winner=0): q = F02 complete-field
  vote-share. w>0 => won more than the market's implied probability. started-all matches the 068
  population.

Shared primitives (adoption != import, FR-004/codex 論点2): u from pm_rank_robust
(rank_percentile_primitive), q from pm_core_strength (race_market_primitive) — never recomputed.
F04 also EXPOSES finish_residual as a primitive for F05 (pm_conditioned residual group).

Two-population NaN gates (frozen, specs/070 gate-config f04_formula, III/analyze U1):
- finish_resid_* : NaN when the FINISHED-observation count < min_obs=3 (INTERNAL finished count,
  NOT the surfaced started count — a many-starts/few-finishes horse must not surface a 1-obs
  high-variance finish residual).
- win_resid_*    : NaN when the started-observation count < min_obs=3.
- asof_pm_resid_sd5 : sample sd (ddof=1) of WIN residual over the last 5 (NaN when obs<2).
- asof_pm_result_obs_count : the surfaced STARTED result-observation count (0 meaningful).

Leak boundary (II): only PAST results × PAST market (strictly-before + same-day excluded via
merge_asof allow_exact_matches=False). Target-race results/odds NEVER enter. ``asof_pm_*`` naming.

POLICY: MARKET/result data -> accuracy-first candidate only; default model drops this group.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames
from .pm_core_strength import race_market_primitive
from .pm_rank_robust import rank_percentile_primitive

PM_EXPECTATION_RESIDUAL_COLUMNS = [
    "asof_pm_finish_resid_mean5",
    "asof_pm_finish_resid_career",
    "asof_pm_win_resid_mean10",
    "asof_pm_win_resid_career",
    "asof_pm_resid_sd5",
    "asof_pm_result_obs_count",
]

_MIN_OBS = 3
_FINISH_WINDOW = 5
_WIN_WINDOW = 10
_SD_WINDOW = 5
_SD_MIN_OBS = 2  # ddof=1 needs >=2


def finish_residual_primitive(
    started: pd.DataFrame, u_prim: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Feature 070 (shared primitive): per (race_id, horse_id, race_date) finish_residual
    ``e = v - u`` for FINISHED past starts in a popularity-complete-field race. F04 aggregates
    these; F05 residual consumes the SAME per-race value (conditioned by surface).

    ``started`` rows carry finish_order/result_status/popularity/race_date. Returns columns
    ``race_id, horse_id, race_date, e``.
    """
    if u_prim is None:
        u_prim = rank_percentile_primitive(started)
    if u_prim.empty:
        return started.iloc[0:0][["race_id", "horse_id", "race_date"]].assign(
            e=pd.Series(dtype=float)
        )
    df = started.merge(u_prim[["race_id", "horse_id", "u", "N"]],
                       on=["race_id", "horse_id"], how="inner")
    finished = df["result_status"] == ResultStatus.FINISHED
    fo = pd.to_numeric(df["finish_order"], errors="coerce")
    df = df[finished & fo.notna()].copy()
    if df.empty:
        return df[["race_id", "horse_id", "race_date"]].assign(e=pd.Series(dtype=float))
    denom = df["N"] - 1.0
    v = np.where(denom > 0, 1.0 - (pd.to_numeric(df["finish_order"]) - 1.0) / denom, 1.0)
    df["e"] = v - df["u"]
    return df[["race_id", "horse_id", "race_date", "e"]]


def _win_residual(started: pd.DataFrame, q_prim: pd.DataFrame) -> pd.DataFrame:
    """Per (race_id, horse_id, race_date) win_residual ``w = I(win) - q`` for STARTED past starts
    in an odds-complete-field race (non-winner=0)."""
    if q_prim.empty:
        return started.iloc[0:0][["race_id", "horse_id", "race_date"]].assign(
            w=pd.Series(dtype=float)
        )
    df = started.merge(q_prim[["race_id", "horse_id", "q"]],
                       on=["race_id", "horse_id"], how="inner")
    finished = df["result_status"] == ResultStatus.FINISHED
    fo = pd.to_numeric(df["finish_order"], errors="coerce")
    win = (finished & (fo == 1)).astype(float)
    df["w"] = win - df["q"]
    return df[["race_id", "horse_id", "race_date", "w"]]


def _asof_finish(e_src: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """As-of reductions of finish_residual over FINISHED population (gate on finished count)."""
    e_src = e_src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = e_src.groupby("horse_id", sort=False)["e"]
    e_src["_fin_cnt"] = g.expanding().count().reset_index(level=0, drop=True)
    e_src["asof_pm_finish_resid_mean5"] = (
        g.rolling(_FINISH_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    e_src["asof_pm_finish_resid_career"] = (
        g.expanding().mean().reset_index(level=0, drop=True)
    )
    cols = ["horse_id", "race_date", "_fin_cnt",
            "asof_pm_finish_resid_mean5", "asof_pm_finish_resid_career"]
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(
        t, e_src[cols].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )


def _asof_win(w_src: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """As-of reductions of win_residual over STARTED population (surfaced result_obs_count)."""
    w_src = w_src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = w_src.groupby("horse_id", sort=False)["w"]
    w_src["asof_pm_result_obs_count"] = g.expanding().count().reset_index(level=0, drop=True)
    w_src["asof_pm_win_resid_mean10"] = (
        g.rolling(_WIN_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    w_src["asof_pm_win_resid_career"] = g.expanding().mean().reset_index(level=0, drop=True)
    w_src["asof_pm_resid_sd5"] = (
        g.rolling(_SD_WINDOW, min_periods=_SD_MIN_OBS).std(ddof=1).reset_index(level=0, drop=True)
    )
    cols = ["horse_id", "race_date", "asof_pm_result_obs_count",
            "asof_pm_win_resid_mean10", "asof_pm_win_resid_career", "asof_pm_resid_sd5"]
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(
        t, w_src[cols].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )


def _empty(targets: pd.DataFrame) -> pd.DataFrame:
    out = targets[["race_id", "horse_id"]].copy()
    for c in PM_EXPECTATION_RESIDUAL_COLUMNS:
        out[c] = 0.0 if c == "asof_pm_result_obs_count" else np.nan
    return out[["race_id", "horse_id", *PM_EXPECTATION_RESIDUAL_COLUMNS]]


def build_pm_expectation_residual_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) F04 as-of features. All aggregate race_date < R (strictly-before)."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    has_pop = "popularity" in frames.race_horses.columns
    has_odds = "odds" in frames.race_horses.columns
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    rh["popularity"] = (frames.race_horses["popularity"].to_numpy() if has_pop
                        else np.full(len(rh), np.nan))
    rh["odds"] = (frames.race_horses["odds"].to_numpy() if has_odds
                  else np.full(len(rh), np.nan))
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]].copy()
    runs = rh.merge(races, on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    started = runs[runs["entry_status"] == EntryStatus.STARTED].copy()
    targets = runs[["race_id", "horse_id", "race_date"]].copy()

    u_prim = rank_percentile_primitive(started)  # popularity complete-field
    q_prim = race_market_primitive(started)       # odds complete-field
    e_src = finish_residual_primitive(started, u_prim)
    w_src = _win_residual(started, q_prim)
    if e_src.empty and w_src.empty:
        return _empty(targets)

    out = targets[["race_id", "horse_id"]].copy()
    if not e_src.empty:
        fin = _asof_finish(e_src, targets)
        # gate finish_resid on the INTERNAL finished count (analyze U1), not the started count
        below = fin["_fin_cnt"].fillna(0.0) < _MIN_OBS
        for c in ("asof_pm_finish_resid_mean5", "asof_pm_finish_resid_career"):
            fin.loc[below, c] = np.nan
        out = out.merge(
            fin[["race_id", "horse_id", "asof_pm_finish_resid_mean5",
                 "asof_pm_finish_resid_career"]],
            on=["race_id", "horse_id"], how="left",
        )
    else:
        out["asof_pm_finish_resid_mean5"] = np.nan
        out["asof_pm_finish_resid_career"] = np.nan
    if not w_src.empty:
        win = _asof_win(w_src, targets)
        win["asof_pm_result_obs_count"] = win["asof_pm_result_obs_count"].fillna(0.0)
        below_w = win["asof_pm_result_obs_count"] < _MIN_OBS
        for c in ("asof_pm_win_resid_mean10", "asof_pm_win_resid_career"):
            win.loc[below_w, c] = np.nan
        out = out.merge(
            win[["race_id", "horse_id", "asof_pm_win_resid_mean10", "asof_pm_win_resid_career",
                 "asof_pm_resid_sd5", "asof_pm_result_obs_count"]],
            on=["race_id", "horse_id"], how="left",
        )
    else:
        for c in ("asof_pm_win_resid_mean10", "asof_pm_win_resid_career", "asof_pm_resid_sd5"):
            out[c] = np.nan
        out["asof_pm_result_obs_count"] = 0.0
    out["asof_pm_result_obs_count"] = out["asof_pm_result_obs_count"].fillna(0.0)
    return out[["race_id", "horse_id", *PM_EXPECTATION_RESIDUAL_COLUMNS]]
