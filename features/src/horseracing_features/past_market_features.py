"""Feature 058 (B1): past-race market-assessment as-of features (accuracy-first model).

A horse's POPULARITY (market rank, derived from odds) at each PAST start distils the crowd's
assessment then. We aggregate it as-of (strictly before the target race, same-day excluded):

- asof_mkt_rank_avg      : recent-N mean past popularity rank (lower = more favored)
- asof_mkt_rank_norm_avg : mean past popularity / field_size (field-size-invariant favor)
- asof_mkt_rank_best     : best (min) past popularity rank
- asof_beat_mkt_avg      : mean (popularity − finish_order) = how much the horse OUTPERFORMED its
                           market rank (positive = beat the market's expectation).

Leak boundary (constitution II): only PAST starts feed these — merge_asof(backward,
allow_exact_matches=False) = strictly-before + same-day excluded. The target race's own popularity
NEVER enters its features. Column names avoid the leak-guard forbidden substrings (odds/popularity),
041 precedent ("late_gain").

POLICY: this group uses MARKET data (popularity), so it is gated behind its own spec (058, B1).
The default decision-support model must NOT include it (keep p⊥q). Accuracy-first model only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames

PAST_MARKET_COLUMNS = [
    "asof_mkt_rank_avg", "asof_mkt_rank_norm_avg", "asof_mkt_rank_best", "asof_beat_mkt_avg",
]
_RECENT_N = 5


def _rolling_asof(
    src: pd.DataFrame, targets: pd.DataFrame, specs: dict[str, tuple[str, str]]
) -> pd.DataFrame:
    """Recent-N rolling agg per horse, then strictly-before as-of merge (023 idiom)."""
    src = src.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = src.groupby("horse_id", sort=False)
    out_cols = ["horse_id", "race_date"]
    for col, (base, agg) in specs.items():
        roll = g[base].rolling(_RECENT_N, min_periods=1)
        series = roll.mean() if agg == "mean" else roll.min()
        src[col] = series.reset_index(level=0, drop=True)
        out_cols.append(col)
    t = targets.sort_values("race_date", kind="stable")
    return pd.merge_asof(
        t, src[out_cols].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )


def build_past_market_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) past-market as-of features. All aggregate race_date < R."""
    races = frames.races[["race_id", "race_date"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    has_pop = "popularity" in frames.race_horses.columns
    rh_cols = ["race_id", "horse_id", "entry_status"]
    if has_pop:
        rh_cols.append("popularity")
    rh = frames.race_horses[rh_cols].copy()
    rr = frames.race_results[["race_id", "horse_id", "finish_order"]].copy()
    runs = rh.merge(races, on="race_id", how="left").merge(
        rr, on=["race_id", "horse_id"], how="left"
    )
    runs["is_started"] = (runs["entry_status"] == EntryStatus.STARTED).astype(int)
    runs["mkt_rank"] = (
        pd.to_numeric(runs["popularity"], errors="coerce") if has_pop
        else pd.Series(np.nan, index=runs.index)
    )
    # field size = started horses per race (for rank normalization)
    fs = runs.groupby("race_id", as_index=False)["is_started"].sum().rename(
        columns={"is_started": "field_size"}
    )
    runs = runs.merge(fs, on="race_id", how="left")
    runs["mkt_rank_norm"] = np.where(
        runs["field_size"] > 0, runs["mkt_rank"] / runs["field_size"], np.nan
    )
    fo = pd.to_numeric(runs["finish_order"], errors="coerce")
    runs["beat_mkt"] = runs["mkt_rank"] - fo  # positive = finished ahead of its market rank

    targets = runs[["race_id", "horse_id", "race_date"]].copy()
    # aggregate over STARTED past races that carry a market rank
    src = runs[(runs["is_started"] == 1) & runs["mkt_rank"].notna()]
    feat = _rolling_asof(
        src, targets,
        {
            "asof_mkt_rank_avg": ("mkt_rank", "mean"),
            "asof_mkt_rank_norm_avg": ("mkt_rank_norm", "mean"),
            "asof_mkt_rank_best": ("mkt_rank", "min"),
            "asof_beat_mkt_avg": ("beat_mkt", "mean"),
        },
    )
    out = targets[["race_id", "horse_id"]].merge(
        feat[["race_id", "horse_id", *PAST_MARKET_COLUMNS]],
        on=["race_id", "horse_id"], how="left",
    )
    return out[["race_id", "horse_id", *PAST_MARKET_COLUMNS]]
