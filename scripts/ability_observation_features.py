"""Research-only observation age/dispersion of the existing clipped speed figure.

No registry, materialization, training, or serving changes are made by this module.
Every source figure uses the existing strictly-before-day condition baseline; the
horse lookup then excludes the whole target day. SD describes past observations,
including changing race conditions, rather than uncertainty of a latent ability.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from horseracing_features.loader import Frames
from horseracing_features.speed_figure_features import _figure_runs

OBSERVATION_COLUMNS = [
    "asof_spdfig_last_age_days",
    "asof_spdfig_best_age_days",
    "asof_spdfig_sd",
]


def build_observation_features(frames: Frames) -> pd.DataFrame:
    """Return keys plus three float64 columns, in original race_horses row order.

    Best-age ties use the latest attainment. SD is expanding population SD
    (ddof=0), requiring two valid past observations. Unavailable values stay NaN.
    The full race/results pool supplies the cross-horse baseline even when
    race_horses contains only a target subset. Inputs are never modified.
    """
    races = frames.races.copy()
    races["race_date"] = pd.to_datetime(races["race_date"]).dt.normalize()
    if races["race_id"].isna().any() or races["race_id"].duplicated().any():
        raise ValueError("Race IDs must be non-null and unique")
    if races["race_date"].isna().any():
        raise ValueError("Every race requires a valid race_date")
    keys = ["race_id", "horse_id"]
    for name, table in [("race_horses", frames.race_horses),
                        ("race_results", frames.race_results)]:
        if table[keys].isna().any().any() or table.duplicated(keys).any():
            raise ValueError(f"{name} appearance keys must be non-null and unique")
    # Include invalid/missing measurements and result-only source appearances.
    # Choosing one after filtering valid figures would hide ambiguous history.
    appearances = pd.concat([frames.race_horses[keys], frames.race_results[keys]])
    appearances = appearances.drop_duplicates().merge(
        races[["race_id", "race_date"]], on="race_id", how="left", validate="many_to_one"
    )
    if appearances["race_date"].isna().any():
        raise ValueError("Appearance references an unknown race")
    if appearances.duplicated(["horse_id", "race_date"]).any():
        raise ValueError("Ambiguous same-horse/same-day appearances")

    targets = frames.race_horses[keys].reset_index(drop=True).merge(
        races[["race_id", "race_date"]], on="race_id", how="left", validate="many_to_one"
    )
    targets["_order"] = np.arange(len(targets))
    # Stable canonical input ordering also fixes floating-point aggregation order.
    canonical = replace(
        frames,
        races=races.sort_values("race_id", kind="stable"),
        race_results=frames.race_results.sort_values(keys, kind="stable"),
    )
    src = _figure_runs(canonical).sort_values(["horse_id", "race_date"], kind="stable")
    if src.empty or targets.empty:
        out = targets[keys].copy()
        for col in OBSERVATION_COLUMNS:
            out[col] = np.nan
        return out.reset_index(drop=True)

    g = src.groupby("horse_id", sort=False)
    src["_last_date"] = src["race_date"]
    is_best = src["spdfig_z"] == g["spdfig_z"].cummax()
    src["_best_date"] = src["race_date"].where(is_best).groupby(src["horse_id"]).ffill()
    src["asof_spdfig_sd"] = (
        g["spdfig_z"].expanding(min_periods=2).std(ddof=0).reset_index(level=0, drop=True)
    )
    out = pd.merge_asof(
        targets.sort_values("race_date", kind="stable"),
        src[["horse_id", "race_date", "_last_date", "_best_date", "asof_spdfig_sd"]]
        .sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    out["asof_spdfig_last_age_days"] = (out["race_date"] - out["_last_date"]).dt.days
    out["asof_spdfig_best_age_days"] = (out["race_date"] - out["_best_date"]).dt.days
    out[OBSERVATION_COLUMNS] = out[OBSERVATION_COLUMNS].astype("float64")
    return out.sort_values("_order", kind="stable")[keys + OBSERVATION_COLUMNS].reset_index(drop=True)
