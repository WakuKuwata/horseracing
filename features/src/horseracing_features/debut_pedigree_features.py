"""Feature 032: debut/low-history × pedigree (leak-safe).

Two leak-safe signals that help the market-blind segment (debut / few-start horses), where own form
is thin and pedigree carries the signal:

- sire_debut_win_rate (NEW information, not in Feature 026): the win rate of the SAME sire's OTHER
  offspring on THEIR debut (first career start), strictly before the target race, self-excluded and
  same-day excluded. 026 only has the sire's OVERALL win rate; "do this sire's progeny fire first
  time out" is a distinct early-maturity signal. Built like 031's pace_scenario — a new conditional
  aggregation over OTHER horses, not a product of existing features.
- gating interactions: is_debut / is_low_history GATED onto 026's sire aptitude, so the model can
  weight pedigree only where own form is thin. These are cheap (a GBM can already split on the
  gate); the bundle's adoption is decided by the pre-registered OOS gate (030 precedent).

Leak boundary (constitution II): sire_debut_win_rate uses only OTHER offspring's debut runs strictly
before R (cumulative via merge_asof allow_exact_matches=False, minus this horse's own debut). The
gating columns are products of existing as-of columns. This module never reads the current race's
finishing-position / result-status / market-price raw columns. Missing → NaN (never 0-filled,
except a CLOSED gate = 0). All float64.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .history import build_history_features
from .loader import Frames
from .pedigree_features import MIN_STARTS, build_pedigree_features
from .pedigree_features import _runs as _ped_runs

_KEYS = ["race_id", "horse_id"]

DEBUT_PEDIGREE_COLUMNS = [
    "sire_debut_win_rate",
    "debut_x_sire_win_rate", "debut_x_sire_dist_band_win_rate",
    "lowhist_x_sire_win_rate", "lowhist_x_sire_dist_band_win_rate",
]


def _sire_debut_win_rate(frames: Frames, *, min_starts: int) -> pd.DataFrame:
    """Per (race_id, horse_id) sire's OTHER-offspring debut win rate, as-of (strictly-before),
    self-excluded, same-day excluded. NaN when the sire is unknown or other-debuts < min_starts."""
    runs = _ped_runs(frames)  # race_date, sire_name (normalized), is_win, is_finished
    rh = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    runs = runs.merge(rh, on=_KEYS, how="left")
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)

    # each horse's DEBUT run = its first STARTED appearance (stable by date then race_id)
    started = runs[runs["is_started"] == 1].sort_values(
        ["horse_id", "race_date", "race_id"], kind="stable")
    started = started.assign(_rk=started.groupby("horse_id", sort=False).cumcount())
    debut = started[started["_rk"] == 0][
        ["horse_id", "race_date", "sire_name", "is_win", "is_finished"]].copy()

    # per-sire cumulative debut (finished) wins/count over time
    daily = (debut.groupby(["sire_name", "race_date"], as_index=False, observed=True)
             .agg(w=("is_win", "sum"), c=("is_finished", "sum"))
             .sort_values(["sire_name", "race_date"], kind="stable"))
    g = daily.groupby("sire_name", sort=False, observed=True)
    daily["cw"] = g["w"].cumsum()
    daily["cc"] = g["c"].cumsum()

    # strictly-before cumulative at each target row (allow_exact_matches=False = same-day excluded)
    targets = runs[["race_id", "horse_id", "race_date", "sire_name"]].sort_values(
        "race_date", kind="stable")
    sire_cum = daily[["sire_name", "race_date", "cw", "cc"]].sort_values("race_date", kind="stable")
    m = pd.merge_asof(
        targets, sire_cum, on="race_date", by="sire_name",
        direction="backward", allow_exact_matches=False)

    # self-exclusion: subtract this horse's own debut iff it falls strictly before R
    selfd = debut.rename(columns={"race_date": "_dd", "is_win": "_sw", "is_finished": "_sc"})
    m = m.merge(selfd[["horse_id", "_dd", "_sw", "_sc"]], on="horse_id", how="left")
    before = (m["_dd"].notna() & (m["_dd"] < m["race_date"])).to_numpy()
    sw = np.where(before, m["_sw"].fillna(0.0).to_numpy(), 0.0)
    sc = np.where(before, m["_sc"].fillna(0.0).to_numpy(), 0.0)
    ow = m["cw"].to_numpy(dtype="float64") - sw
    oc = m["cc"].to_numpy(dtype="float64") - sc
    with np.errstate(invalid="ignore", divide="ignore"):  # masked rows divide by 0/NaN harmlessly
        m["sire_debut_win_rate"] = np.where(oc >= min_starts, ow / oc, np.nan)
    return m[[*_KEYS, "sire_debut_win_rate"]]


def _gate(gate: pd.Series, value: pd.Series) -> np.ndarray:
    """Gated interaction: value where gate==1, else 0.0 (CLOSED gate = 0, not NaN). An open gate
    with an unknown sire stays NaN (genuine missing). Avoids the 0×NaN=NaN trap of a raw product."""
    return np.where(gate.to_numpy() == 1, value.to_numpy(dtype="float64"), 0.0)


def build_debut_pedigree_features(
    frames: Frames, *, history: pd.DataFrame | None = None,
    pedigree: pd.DataFrame | None = None, min_starts: int = MIN_STARTS,
    target_race_ids: frozenset[str] | None = None,
) -> pd.DataFrame:
    """Per (race_id, horse_id) Feature-032 debut_pedigree columns. history/pedigree may be passed
    (precomputed) to avoid recomputation from the materialize chain; otherwise computed here.

    Feature 072: gates read the (already-projected) history/pedigree of the target field; the base
    row set is restricted to the target races. ``sire_debut_win_rate`` (other-offspring, self- and
    same-day excluded) stays computed over the full frame — only target rows are kept.
    Byte-identical on the target rows (INV-P1)."""
    if history is None:
        history = build_history_features(frames, target_race_ids=target_race_ids)
    if pedigree is None:
        pedigree = build_pedigree_features(frames, target_race_ids=target_race_ids)

    sdw = _sire_debut_win_rate(frames, min_starts=min_starts)
    h = history[[*_KEYS, "is_debut", "is_low_history"]]
    p = pedigree[[*_KEYS, "sire_win_rate", "sire_dist_band_win_rate"]]
    base = frames.race_horses[_KEYS].drop_duplicates()
    if target_race_ids is not None:
        base = base[base["race_id"].isin(target_race_ids)]
    out = (base
           .merge(sdw, on=_KEYS, how="left")
           .merge(h, on=_KEYS, how="left")
           .merge(p, on=_KEYS, how="left"))

    out["debut_x_sire_win_rate"] = _gate(out["is_debut"], out["sire_win_rate"])
    out["debut_x_sire_dist_band_win_rate"] = _gate(out["is_debut"], out["sire_dist_band_win_rate"])
    out["lowhist_x_sire_win_rate"] = _gate(out["is_low_history"], out["sire_win_rate"])
    out["lowhist_x_sire_dist_band_win_rate"] = _gate(
        out["is_low_history"], out["sire_dist_band_win_rate"])

    out[DEBUT_PEDIGREE_COLUMNS] = out[DEBUT_PEDIGREE_COLUMNS].astype("float64")
    return out[[*_KEYS, *DEBUT_PEDIGREE_COLUMNS]].sort_values(_KEYS, kind="stable").reset_index(
        drop=True
    )
