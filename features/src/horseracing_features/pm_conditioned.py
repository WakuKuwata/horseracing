"""Feature 070 (F05): CONDITIONED past-market as-of features (accuracy-first model).

Condition the past-market signal on the TARGET race's surface / distance-band / venue: "this horse
is under-rated by the market specifically on dirt sprints". Two registry groups so their column-wise
dependency can be dropped independently (codex B3):

- pm_conditioned_support  (depends on F02): asof_pm_support_{surface,distband,venue} = F02 support
  ``s`` conditioned per axis, + per-axis real-cell counts asof_pm_support_cond_count_{axis}.
- pm_conditioned_residual (depends on F04): asof_pm_finish_resid_surface = F04 finish_residual
  conditioned by surface, + asof_pm_finish_resid_surface_count.

Hierarchical shrinkage (frozen λ=5, specs/070 gate-config f05_formula, III): for the target's cell,
``μ_shrunk = (n_cell·μ_cell + λ·μ_parent)/(n_cell+λ)``. The cell cumulative sum/count and the
overall (parent) cumulative sum/count are fetched SEPARATELY as-of the target (never a pre-shrunk
value, codex 論点1) so the parent never goes stale. n_cell=0 -> parent fallback; parent also empty
(debut) -> NaN (Unknown convention, IV — no 0-substitution, analyze U1). The per-axis valid count is
the REAL cell observation count (parent-fallback rows excluded, codex B5).

Leak boundary (II): past races only (strictly-before + same-day excluded); the parent overall mean
is a per-row expanding cumulative (pool-end independent -> materialize-safe, 025 parity). The
target's own cell KEY is a pre-race static attribute (surface/distband/venue), not a leak.

POLICY: MARKET/result data -> accuracy-first candidate only; default model drops these groups.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .extra_features import _DIST_BINS  # 020/023 dist_band bin edges (analyze M1/T1)
from .loader import Frames
from .pm_core_strength import race_market_primitive
from .pm_expectation_residual import finish_residual_primitive
from .pm_rank_robust import rank_percentile_primitive

PM_CONDITIONED_SUPPORT_COLUMNS = [
    "asof_pm_support_surface",
    "asof_pm_support_distband",
    "asof_pm_support_venue",
    "asof_pm_support_cond_count_surface",
    "asof_pm_support_cond_count_distband",
    "asof_pm_support_cond_count_venue",
]
PM_CONDITIONED_RESIDUAL_COLUMNS = [
    "asof_pm_finish_resid_surface",
    "asof_pm_finish_resid_surface_count",
]
PM_CONDITIONED_COLUMNS = [
    *PM_CONDITIONED_SUPPORT_COLUMNS, *PM_CONDITIONED_RESIDUAL_COLUMNS,
]

_LAMBDA = 5.0


def _axis_values(races: pd.DataFrame) -> pd.DataFrame:
    """Per race the three condition-axis cell keys (surface/distband/venue). distband reuses the
    existing 020/023 dist_band bin edges (analyze M1/T1 — same bins, new column name)."""
    r = races[["race_id"]].copy()
    r["cell_surface"] = races["track_type"].astype("string")
    r["cell_distband"] = pd.cut(
        pd.to_numeric(races["distance"], errors="coerce"), bins=_DIST_BINS, labels=False
    ).astype("Int64").astype("string")
    r["cell_venue"] = races["venue_code"].astype("string")
    return r


def _conditioned_shrunk(
    src: pd.DataFrame, targets: pd.DataFrame, value_col: str, cell_col: str
) -> pd.DataFrame:
    """λ-shrunk conditioned as-of value for the target's cell.

    ``src`` rows: horse_id, race_date, ``cell_col``, ``value_col`` (a past-race value in that cell).
    ``targets`` rows: race_id, horse_id, race_date, ``cell_col`` (the TARGET's cell key). Returns
    targets with ``shrunk`` and ``cell_cnt`` (real cell observations).

    PARENT = the overall all-prior mean over EVERY prior obs (regardless of cell key, incl. past
    races whose axis value is null — codex 実装#1); CELL = only prior obs whose cell matches. Both
    fetched by separate strictly-before merge_asof (codex 論点1). A target with a null cell key
    finds no cell match (n_cell=0) and correctly falls back to the parent mean."""
    src = src.copy()
    # parent cumulative over ALL prior rows (value present, cell key IRRELEVANT) — the overall mean
    ps = src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    gp = ps.groupby("horse_id", sort=False)[value_col]
    ps["par_sum"] = gp.expanding().sum().reset_index(level=0, drop=True)
    ps["par_cnt"] = gp.expanding().count().reset_index(level=0, drop=True)
    par_cols = ps[["horse_id", "race_date", "par_sum", "par_cnt"]].sort_values(
        "race_date", kind="stable"
    )
    # cell cumulative over prior rows with a matching cell key only
    cs = src.dropna(subset=[cell_col]).sort_values(
        ["horse_id", cell_col, "race_date"], kind="stable"
    ).copy()
    gc = cs.groupby(["horse_id", cell_col], sort=False)[value_col]
    cs["cell_sum"] = gc.expanding().sum().reset_index(level=[0, 1], drop=True)
    cs["cell_cnt"] = gc.expanding().count().reset_index(level=[0, 1], drop=True)
    cell_cols = cs[["horse_id", cell_col, "race_date", "cell_sum", "cell_cnt"]].sort_values(
        "race_date", kind="stable"
    )

    t = targets.sort_values("race_date", kind="stable").copy()
    t = pd.merge_asof(
        t, par_cols, on="race_date", by="horse_id",
        direction="backward", allow_exact_matches=False,
    )
    # cell merge only on targets with a non-null cell key (avoids NaN by-key); others -> parent only
    have_cell = t[cell_col].notna()
    if have_cell.any():
        tc = pd.merge_asof(
            t[have_cell].sort_values("race_date", kind="stable"), cell_cols,
            on="race_date", by=["horse_id", cell_col],
            direction="backward", allow_exact_matches=False,
        )
        t = t.merge(tc[["race_id", "horse_id", "cell_sum", "cell_cnt"]],
                    on=["race_id", "horse_id"], how="left")
    else:
        t["cell_sum"] = np.nan
        t["cell_cnt"] = np.nan
    n_cell = t["cell_cnt"].fillna(0.0)
    cell_sum = np.where(n_cell > 0, t["cell_sum"].to_numpy(), 0.0)
    par_mean = t["par_sum"] / t["par_cnt"]  # NaN when par_cnt is 0/NaN (debut) -> shrunk NaN
    t["shrunk"] = (cell_sum + _LAMBDA * par_mean) / (n_cell + _LAMBDA)
    t["cell_cnt"] = n_cell
    out = targets[["race_id", "horse_id"]].merge(
        t[["race_id", "horse_id", "shrunk", "cell_cnt"]], on=["race_id", "horse_id"], how="left",
    )
    out["cell_cnt"] = out["cell_cnt"].fillna(0.0)
    return out


def build_pm_conditioned_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) F05 as-of features (support + residual groups)."""
    races = frames.races[["race_id", "race_date", "track_type", "distance", "venue_code"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    axis = _axis_values(races)
    has_pop = "popularity" in frames.race_horses.columns
    has_odds = "odds" in frames.race_horses.columns
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    rh["popularity"] = (frames.race_horses["popularity"].to_numpy() if has_pop
                        else np.full(len(rh), np.nan))
    rh["odds"] = (frames.race_horses["odds"].to_numpy() if has_odds
                  else np.full(len(rh), np.nan))
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]].copy()
    runs = rh.merge(races[["race_id", "race_date"]], on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    started = runs[runs["entry_status"] == EntryStatus.STARTED].copy()

    targets = runs[["race_id", "horse_id", "race_date"]].merge(axis, on="race_id", how="left")
    out = targets[["race_id", "horse_id"]].copy()

    # --- support group (F02 s conditioned) ---
    q_prim = race_market_primitive(started)  # race_id, horse_id, race_date, q, s, N
    s_src = q_prim.merge(axis, on="race_id", how="left")
    for cell_col, shrunk_col, cnt_col in (
        ("cell_surface", "asof_pm_support_surface", "asof_pm_support_cond_count_surface"),
        ("cell_distband", "asof_pm_support_distband", "asof_pm_support_cond_count_distband"),
        ("cell_venue", "asof_pm_support_venue", "asof_pm_support_cond_count_venue"),
    ):
        if s_src.empty:
            out[shrunk_col] = np.nan
            out[cnt_col] = 0.0
            continue
        r = _conditioned_shrunk(
            s_src[["horse_id", "race_date", cell_col, "s"]].rename(columns={"s": "_v"}),
            targets[["race_id", "horse_id", "race_date", cell_col]], "_v", cell_col,
        )
        out = out.merge(r.rename(columns={"shrunk": shrunk_col, "cell_cnt": cnt_col}),
                        on=["race_id", "horse_id"], how="left")
        out[cnt_col] = out[cnt_col].fillna(0.0)

    # --- residual group (F04 finish_residual conditioned by surface) ---
    u_prim = rank_percentile_primitive(started)
    e_prim = finish_residual_primitive(started, u_prim)  # race_id, horse_id, race_date, e
    if e_prim.empty:
        out["asof_pm_finish_resid_surface"] = np.nan
        out["asof_pm_finish_resid_surface_count"] = 0.0
    else:
        e_src = e_prim.merge(axis[["race_id", "cell_surface"]], on="race_id", how="left")
        r = _conditioned_shrunk(
            e_src[["horse_id", "race_date", "cell_surface", "e"]].rename(columns={"e": "_v"}),
            targets[["race_id", "horse_id", "race_date", "cell_surface"]], "_v", "cell_surface",
        )
        out = out.merge(
            r.rename(columns={"shrunk": "asof_pm_finish_resid_surface",
                              "cell_cnt": "asof_pm_finish_resid_surface_count"}),
            on=["race_id", "horse_id"], how="left",
        )
        out["asof_pm_finish_resid_surface_count"] = (
            out["asof_pm_finish_resid_surface_count"].fillna(0.0)
        )

    return out[["race_id", "horse_id", *PM_CONDITIONED_COLUMNS]]
