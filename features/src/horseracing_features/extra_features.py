"""Feature 020: additional leak-safe horse features (recent form / aptitude / class transition).

All as-of race_date < R (strictly before; same-day excluded) reusing the two proven mechanisms:
- rolling-then-merge_asof(backward, allow_exact_matches=False) for "recent N" aggregates,
- daily-aggregate then (cumsum − current-day) for conditional cumulative aggregates.
The target race itself is an appearance, so subtracting the current day excludes it (and any
same-day duplicate). No results of the target race (or same-day races) enter its own features.
"""

from __future__ import annotations

import unicodedata

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .loader import Frames

EXTRA_COLUMNS = [
    "avg_last3_finish", "recent_win_rate",
    "dist_band_win_rate", "dist_band_avg_finish", "surface_win_rate",
    "class_transition",
]

# coarse, deterministic JRA class ordinal (higher = stronger). Unknown → NaN (no fabricated rank).
#
# Keys are NFKC-normalized (see _normalize_class): the raw DB race_class carries full/half-width
# variants (Ｇ３, ｵｰﾌﾟﾝ, １勝) AND the pre-2019 money naming (500万 == 1勝クラス, 1000万 == 2勝)
# coexisting with the post-2019 win-class naming. Mapping against the raw strings left 55.7% of
# races NaN (only 新馬/未勝利/オープン matched) → class_transition was effectively dead.
# We list both the current DB values and the canonical suffixed forms so the map is robust to either
# source (JRA-VAN raw, netkeiba). JG1/JG2/JG3 (障害重賞) and the ambiguous 重賞 stay unmapped → NaN:
# jumps are a separate ladder (excluded from the flat model) so a jump prior yields no comparable
# class transition.
_CLASS_RANK: dict[str, int] = {
    "新馬": 0, "未勝利": 0,
    "1勝": 1, "1勝クラス": 1, "500万": 1, "500万下": 1,
    "2勝": 2, "2勝クラス": 2, "1000万": 2, "1000万下": 2,
    "3勝": 3, "3勝クラス": 3, "1600万": 3, "1600万下": 3,
    "オープン": 4, "OP": 4, "OP(L)": 4, "L": 4, "リステッド": 4,
    "G3": 5, "GIII": 5, "G2": 6, "GII": 6, "G1": 7, "GI": 7,
}


def _normalize_class(s: pd.Series) -> pd.Series:
    """NFKC (full-width↔half-width kana/digits, e.g. Ｇ３→G3, ｵｰﾌﾟﾝ→オープン, １勝→1勝) + strip.
    NaN stays NaN. Mirrors the 026/056 entity-name normalization; here it aligns the raw race_class
    variants onto the _CLASS_RANK keys so class_transition is not silently NaN for most races."""
    return s.map(lambda v: unicodedata.normalize("NFKC", v).strip() if isinstance(v, str) else v)

_DIST_BINS = [-np.inf, 1400, 1800, 2200, np.inf]   # sprint / mile / mid / long
_RECENT_FORM_N = 5


def _enriched_runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date", "distance", "track_type", "race_class"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    runs = rh.merge(races, on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    runs["is_finished"] = (runs["result_status"] == ResultStatus.FINISHED).astype(int)
    runs["is_win"] = ((runs["is_finished"] == 1) & (runs["finish_order"] == 1)).astype(int)
    runs["finish_for_avg"] = np.where(runs["is_finished"] == 1, runs["finish_order"], np.nan)
    runs["dist_band"] = pd.cut(runs["distance"], bins=_DIST_BINS, labels=False).astype("Int64")
    runs["class_rank"] = _normalize_class(runs["race_class"]).map(_CLASS_RANK).astype("float64")
    return runs


def _recent_form(runs: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    fin = runs[runs["is_finished"] == 1].sort_values(
        ["horse_id", "race_date"], kind="stable"
    ).copy()
    g = fin.groupby("horse_id", sort=False)
    # Same pandas rolling kernel as ``transform(lambda s: s.rolling(...).mean())`` but without the
    # per-group Python callback (bit-identical, ~6x faster on the full pool). ``g[col].rolling``
    # returns a (horse_id, orig_index) MultiIndex; drop the group level to realign to ``fin``.
    fin["avg_last3_finish"] = (
        g["finish_order"].rolling(3, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    fin["recent_win_rate"] = (
        g["is_win"].rolling(_RECENT_FORM_N, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    cols = ["horse_id", "race_date", "avg_last3_finish", "recent_win_rate"]
    t = targets.sort_values("race_date", kind="stable")
    # latest finished race STRICTLY before R; its rolling value = last-N finishes before R.
    return pd.merge_asof(t, fin[cols].sort_values("race_date", kind="stable"),
                         on="race_date", by="horse_id", direction="backward",
                         allow_exact_matches=False)


def _cum_before_by(runs: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Per (keys, date) wins/finished cumulative STRICTLY before that date (cumsum − current)."""
    daily = runs.groupby([*keys, "race_date"], as_index=False, observed=True).agg(
        d_wins=("is_win", "sum"),
        d_cnt=("finish_for_avg", "count"),
        d_finsum=("finish_for_avg", "sum"),
    ).sort_values([*keys, "race_date"], kind="stable")
    g = daily.groupby(keys, sort=False, observed=True)
    for col in ["d_wins", "d_cnt", "d_finsum"]:
        daily[f"{col}_b"] = g[col].cumsum() - daily[col]
    return daily


def _aptitude(runs: pd.DataFrame, targets_with_keys: pd.DataFrame) -> pd.DataFrame:
    out = targets_with_keys
    # distance-band: win_rate + avg_finish among prior finished races in the SAME band
    db = _cum_before_by(runs, ["horse_id", "dist_band"])
    db["dist_band_win_rate"] = np.where(db["d_cnt_b"] > 0, db["d_wins_b"] / db["d_cnt_b"], np.nan)
    db["dist_band_avg_finish"] = np.where(
        db["d_cnt_b"] > 0, db["d_finsum_b"] / db["d_cnt_b"], np.nan
    )
    out = out.merge(db[["horse_id", "dist_band", "race_date",
                        "dist_band_win_rate", "dist_band_avg_finish"]],
                    on=["horse_id", "dist_band", "race_date"], how="left")
    # surface (track_type): win_rate among prior finished races on same surface
    sf = _cum_before_by(runs, ["horse_id", "track_type"])
    sf["surface_win_rate"] = np.where(sf["d_cnt_b"] > 0, sf["d_wins_b"] / sf["d_cnt_b"], np.nan)
    out = out.merge(sf[["horse_id", "track_type", "race_date", "surface_win_rate"]],
                    on=["horse_id", "track_type", "race_date"], how="left")
    return out


def _class_transition(runs: pd.DataFrame, targets_with_keys: pd.DataFrame) -> pd.DataFrame:
    started = (runs[runs["is_started"] == 1][["horse_id", "race_date", "class_rank"]]
               .rename(columns={"class_rank": "prev_class_rank"})
               .sort_values("race_date", kind="stable"))
    t = targets_with_keys.sort_values("race_date", kind="stable")
    out = pd.merge_asof(t, started, on="race_date", by="horse_id", direction="backward",
                        allow_exact_matches=False)
    out["class_transition"] = out["class_rank"] - out["prev_class_rank"]  # NaN if no prev/unknown
    return out.drop(columns=["prev_class_rank"])


def build_extra_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-020 horse features. All as-of race_date < R."""
    runs = _enriched_runs(frames)
    targets = runs[
        ["race_id", "horse_id", "race_date", "dist_band", "track_type", "class_rank"]
    ].copy()

    rf = _recent_form(runs, targets[["race_id", "horse_id", "race_date"]])
    apt = _aptitude(runs, targets[["race_id", "horse_id", "race_date", "dist_band", "track_type"]])
    cls = _class_transition(runs, targets[["race_id", "horse_id", "race_date", "class_rank"]])

    out = (targets[["race_id", "horse_id"]]
           .merge(rf[["race_id", "horse_id", "avg_last3_finish", "recent_win_rate"]],
                  on=["race_id", "horse_id"], how="left")
           .merge(apt[["race_id", "horse_id", "dist_band_win_rate", "dist_band_avg_finish",
                       "surface_win_rate"]], on=["race_id", "horse_id"], how="left")
           .merge(cls[["race_id", "horse_id", "class_transition"]],
                  on=["race_id", "horse_id"], how="left"))
    return out[["race_id", "horse_id", *EXTRA_COLUMNS]]
