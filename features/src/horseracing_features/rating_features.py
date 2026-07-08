"""Feature 062: as-of Elo rating — opponent-quality-adjusted latent ability (leak-safe).

Existing ability features (win_rate / place_rate / avg_finish / ...) count outcomes but ignore
WHO the horse beat. This module carries a per-horse Elo rating updated from finishing order, so
beating strong rivals raises it more than beating weak ones. 059's within-race relativization is
only within the CURRENT field; this is a career-long opponent-quality axis.

Update (research D1): each race is a set of pairwise contests over its valid ranked finishers.
For horse i:  ΔR_i = K/(m−1) · Σ_{j≠i} (S_ij − E_ij),  E_ij = 1/(1+10^((R_j−R_i)/400)),
where S_ij = 1/0.5/0 for i finishing ahead/tie/behind j, and m = number of VALID ranked finishers
(post-exclusion, NOT raw starters — codex #12). K=24, init=1500, scale=400 fixed (no OOS tuning).

Leak boundary (constitution II, INV-R1/R2): a race's feature row records the rating BEFORE that
race's result is applied. Same-day races are frozen (research D3, codex #2/#3): every race on a day
sees that day's MORNING snapshot, and all of the day's deltas are applied only AFTER the whole day.
So the current race, its rivals' same-day results, and any future race never move its rating. The
single chronological pass keeps every row strictly-before dependent → pool-end independent →
materialize-safe (the in-memory build always loads from 2007 with no lower window, so the rating
prefix is always replayed identically; a lower-bound windowed load would break this and is guarded
against). No raw source column beyond finish_order/race_date is read → source_fingerprint unchanged.

Determinism (codex #11): stable sort (race_date, race_id, horse_id), ordered aggregation, float64,
no parallel/unordered reductions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames
from .pace_scenario_features import _loo_mean

_KEYS = ["race_id", "horse_id"]

RATING_COLUMNS = [
    "asof_rating",              # morning-snapshot rating level
    "asof_rating_recent_delta",  # change over the last _RECENT rated starts (momentum)
    "asof_rating_max",          # personal-best rating
    "asof_rating_starts",       # as-of number of rated starts (confidence; debut = 0.0)
]
#: field-relative column (059-style LOO), built in a second pass over the assembled frame.
RATING_VS_FIELD = "asof_rating_vs_field"
ALL_RATING_COLUMNS = [*RATING_COLUMNS, RATING_VS_FIELD]

K_FACTOR = 24.0
INIT_RATING = 1500.0
SCALE = 400.0
_RECENT = 3


def _expected(r_i: float, r_j: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + 10.0 ** ((r_j - r_i) / SCALE))


def build_base_rating_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) the four self-contained rating columns (no field relativization).

    One chronological pass with whole-day freezing. Only started horses get feature rows; ratings
    update from valid ranked finishers only (finish_order not null, result FINISHED).
    """
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]].copy()
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]].copy()

    df = rh.merge(races, on="race_id", how="left").merge(rr, on=_KEYS, how="left")
    df["is_started"] = df["entry_status"] == EntryStatus.STARTED
    # valid ranked finisher: started, FINISHED, finish_order present
    fo = pd.to_numeric(df["finish_order"], errors="coerce")
    df["is_ranked"] = (
        df["is_started"]
        & (df["result_status"] == ResultStatus.FINISHED)
        & fo.notna()
    )
    df["finish_rank"] = fo

    # per-horse mutable state
    rating: dict[str, float] = {}
    best: dict[str, float] = {}
    starts: dict[str, int] = {}
    recent: dict[str, list[float]] = {}  # last _RECENT+1 morning ratings for the delta

    # feature accumulators aligned to df rows (only started rows get non-NaN)
    n = len(df)
    f_rating = np.full(n, np.nan)
    f_delta = np.full(n, np.nan)
    f_max = np.full(n, np.nan)
    f_starts = np.full(n, np.nan)

    # deterministic order: day, race within day, horse within race. Reset the index so every
    # accumulator below is filled and read by SORTED position k (assigning a positional numpy array
    # back to a sorted-order frame otherwise scrambles values — caught by the pool-end test).
    df = df.sort_values(["race_date", "race_id", "horse_id"], kind="stable").reset_index(drop=True)
    started = df["is_started"].to_numpy()
    ranked = df["is_ranked"].to_numpy()
    horse = df["horse_id"].to_numpy()
    day = df["race_date"].to_numpy()
    race = df["race_id"].to_numpy()
    frank = df["finish_rank"].to_numpy()

    def _get(h: str) -> float:
        return rating.get(h, INIT_RATING)

    i = 0
    while i < n:
        # --- one day = [i, j) -------------------------------------------------------------
        j = i
        while j < n and day[j] == day[i]:
            j += 1
        # (1) MORNING snapshot: record features for every started row from the frozen state
        for k in range(i, j):
            if not started[k]:
                continue
            h = horse[k]
            r = _get(h)
            f_rating[k] = r
            f_max[k] = best.get(h, INIT_RATING)
            f_starts[k] = float(starts.get(h, 0))
            # momentum: today's morning rating − morning rating _RECENT prior appearances ago
            # (``recent[h]`` holds PRIOR appearances only; today is appended in step 3). NaN=debut.
            hist = recent.get(h)
            if hist:
                ref = hist[-_RECENT] if len(hist) >= _RECENT else hist[0]
                f_delta[k] = r - ref
        # (2) compute the day's deltas from the SAME frozen snapshot, grouped by race
        day_delta: dict[str, float] = {}
        rk = i
        while rk < j:
            rr_end = rk
            while rr_end < j and race[rr_end] == race[rk]:
                rr_end += 1
            # valid ranked finishers of THIS race
            idx = [k for k in range(rk, rr_end) if ranked[k]]
            m = len(idx)
            if m >= 2:
                rs = np.array([_get(horse[k]) for k in idx])
                fr = np.array([frank[k] for k in idx], dtype=float)
                for a in range(m):
                    others = [b for b in range(m) if b != a]
                    r_j = rs[others]
                    e = _expected(rs[a], r_j)
                    # S: 1 if a finishes ahead (smaller rank), 0.5 tie, 0 behind
                    s_ij = np.where(fr[a] < fr[others], 1.0,
                                    np.where(fr[a] == fr[others], 0.5, 0.0))
                    delta = K_FACTOR / (m - 1) * float(np.sum(s_ij - e))
                    day_delta[horse[idx[a]]] = day_delta.get(horse[idx[a]], 0.0) + delta
            rk = rr_end
        # (3) record today's morning rating into each started horse's history (once per race
        #     appearance), THEN apply the day's deltas AFTER the whole day (codex #2 batched).
        for k in range(i, j):
            if not started[k]:
                continue
            h = horse[k]
            hist = recent.setdefault(h, [])
            hist.append(_get(h))            # today's morning value (unchanged within the day)
            if len(hist) > _RECENT:
                hist.pop(0)
        for h, d in day_delta.items():
            nr = _get(h) + d
            rating[h] = nr
            best[h] = max(best.get(h, INIT_RATING), nr)
            starts[h] = starts.get(h, 0) + 1
        i = j

    out = df[_KEYS].copy()
    out["asof_rating"] = f_rating
    out["asof_rating_recent_delta"] = f_delta
    out["asof_rating_max"] = f_max
    out["asof_rating_starts"] = f_starts
    out = out[out["asof_rating"].notna()]  # started rows only
    for c in RATING_COLUMNS:
        out[c] = out[c].astype("float64")
    return out[[*_KEYS, *RATING_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(drop=True)


def build_rating_features(frames: Frames, *, rating_frame: pd.DataFrame) -> pd.DataFrame:
    """Add the field-relative column (059-style LOO) onto the base rating frame.

    ``rating_frame`` is the assembled as-of frame containing ``asof_rating`` (build_asof_features'
    ``out``). vs_field = asof_rating − leave-one-out mean of the started field's asof_rating. Reads
    no raw current-race column (pure within-race post-processing).
    """
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    df = rating_frame[[*_KEYS, "asof_rating"]].merge(rh, on=_KEYS, how="left")
    df = df.reset_index(drop=True)
    df["is_started"] = (df["entry_status"] == EntryStatus.STARTED).astype(int)
    out = df[_KEYS].copy()
    out[RATING_VS_FIELD] = (df["asof_rating"] - _loo_mean(df, "asof_rating")).astype("float64")
    return out[[*_KEYS, RATING_VS_FIELD]].sort_values(_KEYS, kind="stable").reset_index(drop=True)
