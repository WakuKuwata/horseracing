"""Feature 097: early-mid pace — a per-horse early-pace axis that does not die with the feed.

``race_results.first_3f`` (per-horse first-3F time, JRA-VAN col55) stopped arriving: 2024 96.8% →
2025 74.4% → **2026 0.0%**. netkeiba publishes per-horse finish_time and LAST 3F but never the
first 3F, so the three first3f-derived model inputs (3.0% of split importance) decay as histories
roll over, and only the 1200m identity derivation (first_3f = finish_time − last_3f, exact there)
survives. Kill-test (evidence/first3f-killtest.json, fixed production model): losing the whole axis
costs winner NLL 0.0163; the 1200m derivation alone recovers 25% of it.

This module builds the same axis from inputs that stay ~99.5% supplied at every distance:

    em        = finish_time_s − last_3f            (the horse's time EXCLUDING its last 3F)
    rel_em    = em − mean(em over that race's FINISHERS)
    asof_rel_early_mid_avg / _best = recent-5 rolling mean / min of rel_em over the horse's past
                                     finished runs, strictly before the target race
                                     (same-day excluded)

At 1200m ``em`` IS the first 3F (identity, max error 0.0000s over 187,833 rows); at longer
distances it is "first + middle" — a different physical quantity, but ONE consistent definition
over the whole history (INV-EM1). It is deliberately an INDEPENDENT column set: substituting this
quantity into ``first_3f`` itself was measured to be WORSE than leaving it missing in the current
regime (the booster's split thresholds were learned on real first-3F values — the 017/091 class of
same-column/different-meaning hazard), so the existing ``asof_rel_first3f_*`` columns are left
untouched (INV-EM2) and the model learns this quantity as its own input.

Disclosed redundancy (research D2): every pace aggregate shares one rolling window (``_RECENT_N``)
and one source frame, so ``asof_rel_early_mid_avg ≈ rel_time_avg − rel_last3f_avg`` up to
missing-data patterns. What a tree cannot do in one split is form that difference; ``_best`` (a min
of a difference) does not decompose at all. Whether that is worth two columns is exactly what the
pre-registered gate decides — not this docstring.

Reuses ``_pace_runs`` / ``_rolling_asof`` from pace_features (one as-of implementation, 025).
"""

from __future__ import annotations

import pandas as pd

from .loader import Frames
from .pace_features import _pace_runs, _rolling_asof

EARLY_MID_PACE_COLUMNS: tuple[str, ...] = (
    "asof_rel_early_mid_avg",
    "asof_rel_early_mid_best",
)

_KEYS = ["race_id", "horse_id"]


def early_mid_runs(frames: Frames) -> pd.DataFrame:
    """Per-appearance rows with ``em_s`` and ``rel_em`` added (the run-level primitive).

    ``em_s <= 0`` is a broken input (finish time not longer than its own last 3F) and becomes NaN
    rather than a value — the same refusal the 1200m derivation backfill applies (INV-EM4). NaN in
    either input propagates. The race mean is over FINISHERS of that race only, like every other
    in-race relative in pace_features.
    """
    runs = _pace_runs(frames)
    em = runs["time_s"] - runs["last3f_s"]
    runs["em_s"] = em.where(em > 0)
    fin = runs[runs["is_finished"] == 1]
    means = (
        fin.groupby("race_id", as_index=False)["em_s"].mean()
        .rename(columns={"em_s": "race_mean_em"})
    )
    runs = runs.merge(means, on="race_id", how="left")
    runs["rel_em"] = runs["em_s"] - runs["race_mean_em"]
    return runs


def build_early_mid_pace_features(
    frames: Frames, *, target_race_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Per (race_id, horse_id): ``EARLY_MID_PACE_COLUMNS``, float64, NaN = no information.

    Feature 072 projection: with ``target_race_ids`` only those races' rows are emitted and the
    rolling SOURCE is restricted to their horses — per-horse rolling/merge_asof is independent per
    horse, so the values are byte-identical to the full build's rows (test_projection_parity).
    The in-race relative primitive is still computed over the FULL frame.
    """
    runs = early_mid_runs(frames)
    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    fin = runs[runs["is_finished"] == 1]
    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)]
        fin = fin[fin["horse_id"].isin(frozenset(targets["horse_id"]))]

    feat = _rolling_asof(
        fin, targets,
        {
            "asof_rel_early_mid_avg": ("rel_em", "mean"),
            "asof_rel_early_mid_best": ("rel_em", "min"),
        },
    )
    out = targets[_KEYS].merge(feat[[*_KEYS, *EARLY_MID_PACE_COLUMNS]], on=_KEYS, how="left")
    for c in EARLY_MID_PACE_COLUMNS:
        out[c] = out[c].astype("float64")
    return out[[*_KEYS, *EARLY_MID_PACE_COLUMNS]].reset_index(drop=True)
