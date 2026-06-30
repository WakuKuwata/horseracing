"""Feature 033: condition-change × ability/time (leak-safe).

Feature 027's condition-change base (distance / surface / going vs the most-recent prior STARTED
race) was flat as a standalone group (8/18 folds) and kept on a branch — so it is NEW information
the model does not have. This feature re-introduces that base and (the 032 lesson: a product of
two EXISTING model columns is GBM-redundant, so make NEW base info effective rather than multiply
existing features) converts the distance change into signed hinges and crosses them with the horse's
as-of closing/time ability, so a shallow tree can learn the asymmetric "stretch-out × strong closer"
and "cut-back × fast clock" domains directly.

Columns (all float64, NaN-propagating, 0-fill forbidden):
- base (027): dist_change, surface_switch, going_change.
- hinge: dist_extension = max(dist_change,0), dist_shortening = max(-dist_change,0).
- ability interactions: dist_ext_x_closing = dist_extension × (-rel_last3f_best),
  dist_short_x_speed = dist_shortening × (-rel_time_avg). (rel_* are 023's as-of in-race-relative
  values; lower=better, so the sign is flipped to make "good ability" positive.)

Leak boundary (constitution II): the base compares today's PRE_ENTRY conditions to the most-recent
prior STARTED race (merge_asof allow_exact_matches=False = strictly before, same-day excluded); the
ability is 023's as-of output. This module never reads the current race's finishing-position /
result-status / market-price raw columns. class_transition × time and weight × time are DROPPED
(GBM-redundant — both operands already in the model).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .extra_features import _DIST_BINS
from .loader import Frames
from .pace_features import build_pace_features

_KEYS = ["race_id", "horse_id"]

CONDITION_CHANGE_COLUMNS = [
    "dist_change", "surface_switch", "going_change",
    "dist_extension", "dist_shortening",
    "dist_ext_x_closing", "dist_short_x_speed",
]

#: going state as an ordinal (worse going = larger). Unknown → NaN. Real DB stores single-char
#: abbreviations (良/稍/重/不); full forms also accepted (027).
_GOING_ORD = {"良": 0.0, "稍": 1.0, "稍重": 1.0, "重": 2.0, "不": 3.0, "不良": 3.0}


def _surface(track_type: object) -> str:
    """Coarse surface: 芝→turf, ダ(ート)→dirt, else other (e.g. 障害)."""
    if isinstance(track_type, str):
        if track_type.startswith("芝"):
            return "turf"
        if "ダ" in track_type:
            return "dirt"
    return "other"


def _runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date", "distance", "track_type", "going"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    runs = rh.merge(races, on="race_id", how="left")
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    runs["dist_band"] = pd.cut(runs["distance"], bins=_DIST_BINS, labels=False).astype("Int64")
    runs["surface"] = runs["track_type"].map(_surface)
    runs["going_ord"] = runs["going"].map(_GOING_ORD).astype("float64")
    return runs


def _prev_started(runs: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Most recent STARTED race strictly before R (merge_asof backward, exact matches excluded)."""
    started = (
        runs[runs["is_started"] == 1][["horse_id", "race_date", "distance", "surface", "going_ord"]]
        .rename(columns={"distance": "prev_distance", "surface": "prev_surface",
                         "going_ord": "prev_going_ord"})
        .sort_values("race_date", kind="stable")
    )
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(t, started, on="race_date", by="horse_id", direction="backward",
                         allow_exact_matches=False)


def build_condition_change_features(
    frames: Frames, *, pace: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-033 condition-change × ability columns. base as-of vs the
    prior started race; ability from 023's as-of output (pass `pace` to avoid recomputation)."""
    if pace is None:
        pace = build_pace_features(frames)
    runs = _runs(frames)
    base = runs[_KEYS].copy()
    tr = _prev_started(
        runs, runs[["race_id", "horse_id", "race_date", "distance", "surface", "going_ord"]])

    tr["dist_change"] = tr["distance"] - tr["prev_distance"]            # prev missing → NaN
    tr["going_change"] = tr["going_ord"] - tr["prev_going_ord"]        # either NaN → NaN
    has_prev = tr["prev_surface"].notna()
    cur, prev = tr["surface"], tr["prev_surface"]
    ss = pd.Series(np.nan, index=tr.index, dtype="float64")
    ss[has_prev & (cur == prev)] = 0.0
    ss[has_prev & (cur == "dirt") & (prev == "turf")] = 1.0           # 芝 → ダ
    ss[has_prev & (cur == "turf") & (prev == "dirt")] = -1.0          # ダ → 芝
    ss[has_prev & (cur != prev) & ~((cur == "dirt") & (prev == "turf"))
       & ~((cur == "turf") & (prev == "dirt"))] = 0.0                 # other change (e.g. 障)
    tr["surface_switch"] = ss

    dc = tr["dist_change"]
    notna = dc.notna().to_numpy()
    dc_v = dc.to_numpy(dtype="float64")
    tr["dist_extension"] = np.where(notna, np.maximum(dc_v, 0.0), np.nan)
    tr["dist_shortening"] = np.where(notna, np.maximum(-dc_v, 0.0), np.nan)

    # ability interactions: hinge × (-rel_*) so "good ability" (lower rel) is positive.
    tr = tr.merge(pace[[*_KEYS, "rel_last3f_best", "rel_time_avg"]], on=_KEYS, how="left")
    tr["dist_ext_x_closing"] = tr["dist_extension"] * (-tr["rel_last3f_best"])
    tr["dist_short_x_speed"] = tr["dist_shortening"] * (-tr["rel_time_avg"])

    out = base.merge(tr[[*_KEYS, *CONDITION_CHANGE_COLUMNS]], on=_KEYS, how="left")
    out[CONDITION_CHANGE_COLUMNS] = out[CONDITION_CHANGE_COLUMNS].astype("float64")
    return out[[*_KEYS, *CONDITION_CHANGE_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(
        drop=True
    )
