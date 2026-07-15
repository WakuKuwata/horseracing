"""Feature 030: low-cost as-of features from already-ingested columns (leak-safe).

Groups (as-of part; the static 斤量/季節 live in static_features):
- handicap: carried_weight_change (今走−前走 斤量, merge_asof).
- place_rate: place_rate(top2)/show_rate(top3)/dist_band_place_rate — 自馬 strictly-before.
- human_form_plus: jockey/trainer 複勝率・直近・(jockey,track_type)・(jockey_id,trainer_id) コンビ・
  乗り替わり(今走 vs 直前 started race の騎手) — 跨馬統計は対象行+同日除外(human_form 同型).
- course_aptitude: (horse_id, venue_code) 自馬 as-of 勝率/複勝率.

All as-of via the proven mechanisms (daily cumsum − current-day; merge_asof backward,
allow_exact_matches=False). NEVER uses the target race's own result, nor any result-derived
running-style / corner-order columns. All columns float64 (parity).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from .extra_features import _DIST_BINS
from .loader import Frames

_KEYS = ["race_id", "horse_id"]
_RECENT_N = 20  # jockey recent-form window (finished mounts)
MIN_STARTS = 5  # conditional rate (dist-band/surface/combo/venue) needs this many before → else NaN

OUTPUT_COLUMNS = [
    "carried_weight_change",
    "place_rate", "show_rate", "dist_band_place_rate",
    "jockey_place_rate", "trainer_place_rate", "jockey_recent_win_rate",
    "jockey_surface_win_rate", "jt_combo_win_rate", "jockey_change",
    "venue_win_rate", "venue_place_rate",
]


def _runs(frames: Frames) -> pd.DataFrame:
    races = frames.races[["race_id", "race_date", "distance", "track_type", "venue_code"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[
        ["race_id", "horse_id", "entry_status", "jockey_id", "trainer_id", "jockey_weight"]
    ]
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    runs = rh.merge(races, on="race_id", how="left").merge(rr, on=_KEYS, how="left")
    fin = (runs["result_status"] == ResultStatus.FINISHED)
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    runs["is_finished"] = fin.astype(int)
    runs["is_win"] = (fin & (runs["finish_order"] == 1)).astype(int)
    runs["is_top2"] = (fin & (runs["finish_order"] <= 2)).astype(int)
    runs["is_top3"] = (fin & (runs["finish_order"] <= 3)).astype(int)
    runs["fin_cnt"] = np.where(fin, 1.0, np.nan)
    runs["dist_band"] = pd.cut(runs["distance"], bins=_DIST_BINS, labels=False).astype("Int64")
    runs["cw"] = pd.to_numeric(runs["jockey_weight"], errors="coerce").astype("float64")
    return runs


def _rate_before(runs: pd.DataFrame, keys: list[str], hit: str, out: str,
                 *, min_count: int = 1) -> pd.DataFrame:
    """Per (keys, date) hit-rate over finished prior rows STRICTLY before (cumsum − current-day)."""
    daily = runs.groupby([*keys, "race_date"], as_index=False, observed=True).agg(
        h=(hit, "sum"), c=("fin_cnt", "count"),
    ).sort_values([*keys, "race_date"], kind="stable")
    g = daily.groupby(keys, sort=False, observed=True)
    daily["h_b"] = g["h"].cumsum() - daily["h"]
    daily["c_b"] = g["c"].cumsum() - daily["c"]
    daily[out] = np.where(daily["c_b"] >= min_count, daily["h_b"] / daily["c_b"], np.nan)
    return daily[[*keys, "race_date", out]]


def _prev_started(runs: pd.DataFrame, targets: pd.DataFrame, attr: str) -> pd.DataFrame:
    """Per (race_id, horse_id) value of `attr` at the horse's most recent STARTED race strictly
    before R. Returns a KEYED frame [race_id, horse_id, prev_attr] (merge by keys, not position)."""
    started = (runs[runs["is_started"] == 1][["horse_id", "race_date", attr]]
               .rename(columns={attr: f"prev_{attr}"}).sort_values("race_date", kind="stable"))
    t = targets.sort_values("race_date", kind="stable")
    merged = pd.merge_asof(t, started, on="race_date", by="horse_id", direction="backward",
                           allow_exact_matches=False)
    return merged[["race_id", "horse_id", f"prev_{attr}"]]


def _recent_rate(runs: pd.DataFrame, targets: pd.DataFrame, by: str, hit: str, out: str,
                 n: int) -> pd.DataFrame:
    fin = runs[runs["is_finished"] == 1].sort_values([by, "race_date"], kind="stable").copy()
    fin[out] = fin.groupby(by, sort=False)[hit].transform(
        lambda s: s.rolling(n, min_periods=1).mean()
    )
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(t, fin[[by, "race_date", out]].sort_values("race_date", kind="stable"),
                         on="race_date", by=by, direction="backward", allow_exact_matches=False)


def build_lowcost_features(
    frames: Frames, *, min_starts: int = MIN_STARTS,
    target_race_ids: frozenset[str] | None = None,
) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-030 as-of columns. All as-of race_date < R.

    Feature 072: MIXED per-horse + cross-entity. Restrict the source to rows whose horse_id,
    jockey_id OR trainer_id is a target entity — every per-horse (place/venue/handicap) and cross
    (jockey/trainer/jt-combo) aggregation then has each relevant key's whole history, so the target
    rows stay byte-identical (INV-P1). Output columns are already float64-pinned."""
    runs = _runs(frames)
    tk = runs[["race_id", "horse_id", "race_date", "dist_band", "track_type", "venue_code",
               "jockey_id", "trainer_id", "cw"]].copy()
    if target_race_ids is not None:
        tk = tk[tk["race_id"].isin(target_race_ids)]
        th, tj, tt = (frozenset(tk["horse_id"]), frozenset(tk["jockey_id"].dropna()),
                      frozenset(tk["trainer_id"].dropna()))
        runs = runs[runs["horse_id"].isin(th) | runs["jockey_id"].isin(tj)
                    | runs["trainer_id"].isin(tt)]
    base = tk[_KEYS].copy()
    out = base.copy()

    # --- self place / show (overall) + dist-band place (conditional) ---
    for hit, col in [("is_top2", "place_rate"), ("is_top3", "show_rate")]:
        d = _rate_before(runs, ["horse_id"], hit, col)
        out = out.merge(tk.merge(d, on=["horse_id", "race_date"], how="left")[[*_KEYS, col]],
                        on=_KEYS, how="left")
    d = _rate_before(runs, ["horse_id", "dist_band"], "is_top2", "dist_band_place_rate",
                     min_count=min_starts)
    out = out.merge(
        tk.merge(d, on=["horse_id", "dist_band", "race_date"], how="left")[
            [*_KEYS, "dist_band_place_rate"]], on=_KEYS, how="left")

    # --- human_form_plus (cross-entity; cumsum−current-day = target-row + same-day excluded) ---
    for keys, hit, col, mc, mergecols in [
        (["jockey_id"], "is_top2", "jockey_place_rate", 1, ["jockey_id", "race_date"]),
        (["trainer_id"], "is_top2", "trainer_place_rate", 1, ["trainer_id", "race_date"]),
        (["jockey_id", "track_type"], "is_win", "jockey_surface_win_rate", min_starts,
         ["jockey_id", "track_type", "race_date"]),
        (["jockey_id", "trainer_id"], "is_win", "jt_combo_win_rate", min_starts,
         ["jockey_id", "trainer_id", "race_date"]),
    ]:
        d = _rate_before(runs, keys, hit, col, min_count=mc)
        out = out.merge(tk.merge(d, on=mergecols, how="left")[[*_KEYS, col]], on=_KEYS, how="left")

    jr = _recent_rate(runs, tk[["race_id", "horse_id", "race_date", "jockey_id"]],
                      "jockey_id", "is_win", "jockey_recent_win_rate", _RECENT_N)
    out = out.merge(jr[[*_KEYS, "jockey_recent_win_rate"]], on=_KEYS, how="left")

    # --- course aptitude (self venue, conditional) ---
    for hit, col in [("is_win", "venue_win_rate"), ("is_top2", "venue_place_rate")]:
        d = _rate_before(runs, ["horse_id", "venue_code"], hit, col, min_count=min_starts)
        out = out.merge(
            tk.merge(d, on=["horse_id", "venue_code", "race_date"], how="left")[[*_KEYS, col]],
            on=_KEYS, how="left")

    # --- handicap change + jockey change (prev started race, merge_asof; KEY-based join) ---
    tgt = tk[["race_id", "horse_id", "race_date"]]
    ch = (tk[[*_KEYS, "cw", "jockey_id"]]
          .merge(_prev_started(runs, tgt, "cw"), on=_KEYS, how="left")
          .merge(_prev_started(runs, tgt, "jockey_id"), on=_KEYS, how="left"))
    ch["carried_weight_change"] = ch["cw"] - ch["prev_cw"]
    ch["jockey_change"] = np.where(
        ch["prev_jockey_id"].isna(), np.nan,
        (ch["jockey_id"] != ch["prev_jockey_id"]).astype("float64"),
    )
    out = out.merge(ch[[*_KEYS, "carried_weight_change", "jockey_change"]], on=_KEYS, how="left")

    out[OUTPUT_COLUMNS] = out[OUTPUT_COLUMNS].astype("float64")
    return out[[*_KEYS, *OUTPUT_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(drop=True)
