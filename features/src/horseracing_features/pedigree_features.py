"""Feature 026: sire / damsire aptitude (cross-horse, as-of, OTHER-offspring only).

The signal is "how the SAME sire's OTHER offspring ran, strictly before the target race": a value
even for debut horses (whose own record is empty) — the lever the market underweights.

CRITICAL leak boundary (constitution II):
- strictly-before: per (key, date) cumulative via daily (cumsum − current-day), so the target day
  (and any same-day offspring) is excluded — identical mechanism to Feature 020 human_form.
- SELF-EXCLUDED: a sire's offspring pool INCLUDES the target horse itself. Counting the target
  horse's own past races would double-count its history (history features already cover that) and
  reinforce the pedigree signal with the horse's own record. We subtract the horse's own
  strictly-before cumulative from the sire's: `other = sire_cumulative − self_cumulative`. O(n), no
  per-pair expansion. denom 0 (no other offspring) → NaN; conditional denom < MIN_STARTS → NaN.

Aggregation key = sire_name / damsire_name (the NAME columns are ~100% populated in the real DB;
the *_id columns are ~0% — ID-based keys are deferred). Odds / current-race results never enter.
"""

from __future__ import annotations

import unicodedata

import numpy as np
import pandas as pd
from horseracing_db.enums import ResultStatus

from .extra_features import _DIST_BINS
from .loader import Frames


def _normalize_name(s: pd.Series) -> pd.Series:
    """NFKC (full-width↔half-width, etc.) + strip; NaN stays NaN. Reduces one sire splitting across
    width/spacing variants when keying on the name (sire_id is unpopulated; codex R2). Deterministic
    and applied in the single build path, so materialize↔in-memory parity is unaffected.
    """
    return s.map(lambda v: unicodedata.normalize("NFKC", v).strip() if isinstance(v, str) else v)

#: min OTHER-offspring finished count for a conditional (dist-band / surface) rate; below → NaN.
#: A fixed module constant (part of the feature definition): NOT a runtime arg, so the materialize
#: path and the in-memory path use the same value and stay bit-identical (Feature 025 parity).
MIN_STARTS = 10

SIRE_COLUMNS = [
    "sire_win_rate", "sire_avg_finish", "sire_starts",
    "sire_dist_band_win_rate", "sire_surface_win_rate",
]
DAMSIRE_COLUMNS = ["damsire_win_rate", "damsire_avg_finish"]
PEDIGREE_COLUMNS = [*SIRE_COLUMNS, *DAMSIRE_COLUMNS]

_KEYS = ["race_id", "horse_id"]


def _runs(frames: Frames) -> pd.DataFrame:
    """One row per appearance with sire/damsire attached + as-of aggregation helpers."""
    races = frames.races[["race_id", "race_date", "distance", "track_type"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id"]]
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    ped_cols = ["horse_id", "sire_name", "damsire_name"]
    if len(frames.horses):
        ped = frames.horses[ped_cols]
    else:  # optional/empty horses → all-NaN pedigree (backward compatible)
        ped = pd.DataFrame(columns=ped_cols)
    runs = (
        rh.merge(races, on="race_id", how="left")
        .merge(rr, on=["race_id", "horse_id"], how="left")
        .merge(ped, on="horse_id", how="left")
    )
    for c in ("sire_name", "damsire_name"):  # canonicalize aggregation keys (codex R2)
        runs[c] = _normalize_name(runs[c])
    runs["is_finished"] = (runs["result_status"] == ResultStatus.FINISHED).astype(int)
    runs["is_win"] = ((runs["is_finished"] == 1) & (runs["finish_order"] == 1)).astype(int)
    runs["finish_for_avg"] = np.where(runs["is_finished"] == 1, runs["finish_order"], np.nan)
    runs["dist_band"] = pd.cut(runs["distance"], bins=_DIST_BINS, labels=False).astype("Int64")
    return runs


def _cum_before_by(runs: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Per (keys, date) wins/cnt/finsum cumulative STRICTLY before that date (cumsum − current)."""
    daily = runs.groupby([*keys, "race_date"], as_index=False, observed=True).agg(
        d_wins=("is_win", "sum"),
        d_cnt=("finish_for_avg", "count"),
        d_finsum=("finish_for_avg", "sum"),
    ).sort_values([*keys, "race_date"], kind="stable")
    g = daily.groupby(keys, sort=False, observed=True)
    for col in ["d_wins", "d_cnt", "d_finsum"]:
        daily[f"{col}_b"] = g[col].cumsum() - daily[col]
    return daily


def _other_offspring(
    targets: pd.DataFrame, runs: pd.DataFrame, key: str, extra: list[str] | None = None,
) -> pd.DataFrame:
    """other-offspring (self-excluded) strictly-before wins_b/cnt_b/finsum_b for `key` aggregation.

    `extra` adds a conditioning column (e.g. dist_band / track_type) keyed on BOTH sire and self.
    Returns targets with columns o_wins/o_cnt/o_finsum (NaN if the sire/key is unknown).
    """
    extra = extra or []
    sire = _cum_before_by(runs, [key, *extra])
    self_ = _cum_before_by(runs, ["horse_id", *extra])
    on_sire = [key, *extra, "race_date"]
    on_self = ["horse_id", *extra, "race_date"]
    out = targets.merge(
        sire[[*on_sire, "d_wins_b", "d_cnt_b", "d_finsum_b"]], on=on_sire, how="left"
    ).merge(
        self_[[*on_self, "d_wins_b", "d_cnt_b", "d_finsum_b"]],
        on=on_self, how="left", suffixes=("_s", "_self"),
    )
    # self contribution missing (horse has no finished history) → 0; sire missing (unknown) → NaN.
    for c in ["d_wins_b_self", "d_cnt_b_self", "d_finsum_b_self"]:
        out[c] = out[c].fillna(0.0)
    out["o_wins"] = out["d_wins_b_s"] - out["d_wins_b_self"]
    out["o_cnt"] = out["d_cnt_b_s"] - out["d_cnt_b_self"]
    out["o_finsum"] = out["d_finsum_b_s"] - out["d_finsum_b_self"]
    return out


def build_pedigree_features(
    frames: Frames, *, min_starts: int = MIN_STARTS,
    target_race_ids: frozenset[str] | None = None,
) -> pd.DataFrame:
    """Per (race_id, horse_id) sire/damsire aptitude. All as-of race_date < R, self-excluded.

    Feature 072 (cross-entity): restrict the source to rows whose sire OR damsire is an entity of a
    target horse. That set includes every target horse's own rows (their sire is a target sire), so
    the self-exclusion (other-offspring = sire cumsum − self) and the sire/damsire aggregations stay
    byte-identical on the target rows (INV-P1)."""
    runs = _runs(frames)
    targets = runs[
        ["race_id", "horse_id", "race_date", "sire_name", "damsire_name",
         "dist_band", "track_type"]
    ].copy()
    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)]
        tsire = frozenset(targets["sire_name"].dropna())
        tdam = frozenset(targets["damsire_name"].dropna())
        runs = runs[runs["sire_name"].isin(tsire) | runs["damsire_name"].isin(tdam)]
    # base row set = the (target) appearances; equals runs[_KEYS] in the full build (targets is
    # runs[cols]) and the target rows when projecting.
    base = targets[["race_id", "horse_id"]].copy()

    # sire overall
    so = _other_offspring(targets, runs, "sire_name")
    so["sire_win_rate"] = np.where(so["o_cnt"] > 0, so["o_wins"] / so["o_cnt"], np.nan)
    so["sire_avg_finish"] = np.where(so["o_cnt"] > 0, so["o_finsum"] / so["o_cnt"], np.nan)
    so["sire_starts"] = so["o_cnt"]  # other-offspring finished count (NaN if sire unknown)

    # sire by distance band (conditional; min_starts gate)
    sd = _other_offspring(targets, runs, "sire_name", extra=["dist_band"])
    sd["sire_dist_band_win_rate"] = np.where(
        sd["o_cnt"] >= min_starts, sd["o_wins"] / sd["o_cnt"], np.nan
    )
    # sire by surface (track_type)
    ss = _other_offspring(targets, runs, "sire_name", extra=["track_type"])
    ss["sire_surface_win_rate"] = np.where(
        ss["o_cnt"] >= min_starts, ss["o_wins"] / ss["o_cnt"], np.nan
    )

    # damsire overall (BMS; optional group)
    do = _other_offspring(targets, runs, "damsire_name")
    do["damsire_win_rate"] = np.where(do["o_cnt"] > 0, do["o_wins"] / do["o_cnt"], np.nan)
    do["damsire_avg_finish"] = np.where(do["o_cnt"] > 0, do["o_finsum"] / do["o_cnt"], np.nan)

    out = (
        base
        .merge(so[[*_KEYS, "sire_win_rate", "sire_avg_finish", "sire_starts"]],
               on=_KEYS, how="left")
        .merge(sd[[*_KEYS, "sire_dist_band_win_rate"]], on=_KEYS, how="left")
        .merge(ss[[*_KEYS, "sire_surface_win_rate"]], on=_KEYS, how="left")
        .merge(do[[*_KEYS, "damsire_win_rate", "damsire_avg_finish"]], on=_KEYS, how="left")
    )
    # Stable float64 dtype regardless of pool: every pedigree column is nullable (NaN when the
    # sire/damsire is unknown or below threshold), incl. sire_starts (a count). Without this, a pool
    # that happens to contain no unknown-sire rows yields int64 while the full pool yields float64,
    # breaking materialize↔in-memory bit parity (Feature 025).
    out[PEDIGREE_COLUMNS] = out[PEDIGREE_COLUMNS].astype("float64")
    return out[[*_KEYS, *PEDIGREE_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(drop=True)
