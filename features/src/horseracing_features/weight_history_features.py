"""Leak-safe previous body-weight feature."""

from __future__ import annotations

import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames

_KEYS = ["race_id", "horse_id"]


def build_weight_history_features(
    frames: Frames, *, target_race_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Return each horse's most recent valid weight from a strictly earlier race date.

    A source date with multiple eligible appearances for the same horse is retained as an
    ambiguous NaN marker.  This prevents ``merge_asof`` from silently choosing one candidate or
    falling through to an older, unambiguous weight.
    """
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    runs = frames.race_horses[["race_id", "horse_id", "entry_status", "weight"]].merge(
        races, on="race_id", how="left"
    )

    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)]

    runs["weight"] = pd.to_numeric(runs["weight"], errors="coerce").astype("float64")
    eligible = runs[
        (runs["entry_status"] == EntryStatus.STARTED)
        & runs["weight"].notna()
        & runs["weight"].between(200, 800, inclusive="both")
    ]
    source = eligible.groupby(["horse_id", "race_date"], as_index=False, sort=False).agg(
        prev_weight=("weight", "first"), candidate_count=("weight", "size")
    )
    source.loc[source["candidate_count"] > 1, "prev_weight"] = float("nan")
    source = source[["horse_id", "race_date", "prev_weight"]].sort_values(
        "race_date", kind="stable"
    )

    merged = pd.merge_asof(
        targets.sort_values("race_date", kind="stable"),
        source,
        on="race_date",
        by="horse_id",
        direction="backward",
        allow_exact_matches=False,
    )
    merged["prev_weight"] = merged["prev_weight"].astype("float64")
    return merged[[*_KEYS, "prev_weight"]].sort_values(_KEYS, kind="stable").reset_index(drop=True)
