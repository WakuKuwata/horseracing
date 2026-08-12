"""Feature 090: leak-safe sire × damsire nick residuals.

The two nested partial-pooling strengths are frozen at 350.  They come from the empirical-Bayes
optimum ``lambda* = sigma^2_within / sigma^2_between = 0.0661 / 0.000185 ~= 357``, rounded before
implementation.  Feature 070's lambda=5.0 is deliberately not reused: it is two orders too small
for these sparse cross cells and gives ordinary zero-win samples a large, noisy negative tail.

Every primitive is recomputed as-of the target date, excludes the whole target day, and subtracts
the target horse's own history.  L1 (sire × damsire line) additionally leaves the target L0
(sire × damsire) child cell out before L0 is shrunk toward it.  There is no hard cell threshold.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus

from .loader import Frames
from .pedigree_features import _normalize_name, _other_offspring, _runs

LAMBDA_L0 = 350.0
LAMBDA_L1 = 350.0
EPS_LO = 1e-4
EPS_HI = 0.9

NICK_CROSS_COLUMNS = ["nick_lift_log", "nick_obs_count"]

_KEYS = ["race_id", "horse_id"]
_TARGET_COLUMNS = [*_KEYS, "race_date", "sire_name", "damsire_name", "damsire_line"]


def _nick_runs(frames: Frames) -> pd.DataFrame:
    """Return the shared pedigree run frame with line and eligibility helpers attached."""
    runs = _runs(frames)

    if len(frames.horses) and "damsire_line" in frames.horses.columns:
        lines = frames.horses[["horse_id", "damsire_line"]].copy()
        lines["damsire_line"] = _normalize_name(lines["damsire_line"])
        runs = runs.merge(lines, on="horse_id", how="left")
    else:
        runs["damsire_line"] = np.nan

    entries = frames.race_horses[["race_id", "horse_id", "entry_status"]]
    runs = runs.merge(entries, on=_KEYS, how="left")
    eligible = (runs["entry_status"] == EntryStatus.STARTED) & (runs["is_finished"] == 1)
    runs["is_finished"] = eligible.astype(int)
    runs["is_win"] = (eligible & (runs["finish_order"] == 1)).astype(int)
    runs["finish_for_avg"] = np.where(eligible, runs["finish_order"], np.nan)
    return runs


def _counts(
    targets: pd.DataFrame,
    runs: pd.DataFrame,
    key: str,
    prefix: str,
    *,
    extra: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch one independent strictly-before, self-excluded win/count primitive."""
    stats = _other_offspring(targets, runs, key, extra=extra)
    return stats[[*_KEYS, "o_wins", "o_cnt"]].rename(
        columns={"o_wins": f"{prefix}_wins", "o_cnt": f"{prefix}_cnt"}
    )


def _empirical_rate(wins: pd.Series, counts: pd.Series) -> np.ndarray:
    """Raw win rate, with an empty valid-key history represented by the clip-floor path.

    A zero-history marginal has no observations to distinguish it from zero wins.  Using zero for
    that primitive makes the specified expected-rate clip yield ``EPS_LO`` and, when all hierarchy
    counts are also zero, an exactly neutral residual.  Missing pedigree keys are masked to NaN at
    the output boundary instead of passing through this convention.
    """
    w = wins.fillna(0.0).to_numpy(dtype="float64")
    n = counts.fillna(0.0).to_numpy(dtype="float64")
    rate = np.zeros(len(w), dtype="float64")
    np.divide(w, n, out=rate, where=n > 0.0)
    return rate


def build_nick_cross_features(
    frames: Frames, *, target_race_ids: frozenset[str] | None = None
) -> pd.DataFrame:
    """Build per-appearance nick residual and L0 observation count.

    ``target_race_ids=None`` is the full-build parity reference.  A projection restricts keyed
    history to the sire/damsire entities needed by target horses.  The global overall-rate
    primitive intentionally continues to use all history, because filtering it would change the
    expected rate relative to a restricted full build.
    """
    all_runs = _nick_runs(frames)
    targets = all_runs[_TARGET_COLUMNS].copy()
    if target_race_ids is not None:
        targets = targets[targets["race_id"].isin(target_race_ids)].copy()

    if targets.empty:
        out = targets[_KEYS].copy()
        for column in NICK_CROSS_COLUMNS:
            out[column] = pd.Series(index=out.index, dtype="float64")
        return out[[*_KEYS, *NICK_CROSS_COLUMNS]].reset_index(drop=True)

    keyed_runs = all_runs
    if target_race_ids is not None:
        target_sires = frozenset(targets["sire_name"].dropna())
        target_damsires = frozenset(targets["damsire_name"].dropna())
        keyed_runs = all_runs[
            all_runs["sire_name"].isin(target_sires)
            | all_runs["damsire_name"].isin(target_damsires)
        ]

    # Each marginal is fetched independently.  In particular, no shrunk value is cached as the
    # parent of another shrinkage step.
    sire = _counts(targets, keyed_runs, "sire_name", "sire")
    damsire = _counts(targets, keyed_runs, "damsire_name", "damsire")

    # Global p_overall is a global primitive even under projection (INV-N6 parity).
    overall_targets = targets.assign(_overall_key=0)
    overall_runs = all_runs.assign(_overall_key=0)
    overall = _counts(overall_targets, overall_runs, "_overall_key", "overall")

    l0 = _counts(
        targets,
        keyed_runs,
        "sire_name",
        "l0",
        extra=["damsire_name"],
    )
    l1 = _counts(
        targets,
        keyed_runs,
        "sire_name",
        "l1",
        extra=["damsire_line"],
    )
    # L1 groups by damsire_line, so runs whose line is unknown are dropped from the parent while
    # L0 still counts them. Subtracting the unrestricted child from that parent breaks the
    # containment L0 ⊆ L1 and can drive the leave-child-out counts NEGATIVE — measured exposure is
    # 36.7% of started rows, because 391 of 2,535 damsires carry a line for some horses and none
    # for others. Take the child on the SAME line-known population so the difference stays a real
    # subset difference.
    line_runs = keyed_runs[keyed_runs["damsire_line"].notna()]
    l0_in_l1 = _counts(
        targets,
        line_runs,
        "sire_name",
        "l0line",
        extra=["damsire_name"],
    )

    work = targets[_TARGET_COLUMNS].copy()
    for primitive in (sire, damsire, overall, l0, l1, l0_in_l1):
        work = work.merge(primitive, on=_KEYS, how="left")

    p_sire = _empirical_rate(work["sire_wins"], work["sire_cnt"])
    p_damsire = _empirical_rate(work["damsire_wins"], work["damsire_cnt"])
    p_overall = _empirical_rate(work["overall_wins"], work["overall_cnt"])
    independence = np.zeros(len(work), dtype="float64")
    np.divide(
        p_sire * p_damsire,
        p_overall,
        out=independence,
        where=p_overall > 0.0,
    )
    expected = np.clip(independence, EPS_LO, EPS_HI)

    w_l0 = work["l0_wins"].fillna(0.0).to_numpy(dtype="float64")
    n_l0 = work["l0_cnt"].fillna(0.0).to_numpy(dtype="float64")
    w_l1 = work["l1_wins"].fillna(0.0).to_numpy(dtype="float64")
    n_l1 = work["l1_cnt"].fillna(0.0).to_numpy(dtype="float64")

    w_l0line = work["l0line_wins"].fillna(0.0).to_numpy(dtype="float64")
    n_l0line = work["l0line_cnt"].fillna(0.0).to_numpy(dtype="float64")

    # Both L0 and L1 already exclude the target horse.  Subtracting the line-known child from the
    # line-keyed parent therefore removes the target's own cross from L1 without reintroducing
    # self history, and — because both sides are restricted to line-known runs — stays a genuine
    # subset difference (never negative). The clip is a belt-and-braces guard on float error.
    has_l1_key = work["damsire_line"].notna().to_numpy()
    w_l1_excl = np.where(has_l1_key, np.maximum(w_l1 - w_l0line, 0.0), 0.0)
    n_l1_excl = np.where(has_l1_key, np.maximum(n_l1 - n_l0line, 0.0), 0.0)
    mu_l1 = (w_l1_excl + LAMBDA_L1 * expected) / (n_l1_excl + LAMBDA_L1)
    mu_l0 = (w_l0 + LAMBDA_L0 * mu_l1) / (n_l0 + LAMBDA_L0)

    valid_key = (work["sire_name"].notna() & work["damsire_name"].notna()).to_numpy()
    lift = np.log(mu_l0) - np.log(expected)
    work["nick_lift_log"] = np.where(valid_key, lift, np.nan)
    work["nick_obs_count"] = np.where(valid_key, n_l0, np.nan)
    work[NICK_CROSS_COLUMNS] = work[NICK_CROSS_COLUMNS].astype("float64")

    return (
        work[[*_KEYS, *NICK_CROSS_COLUMNS]]
        .sort_values(_KEYS, kind="stable")
        .reset_index(drop=True)
    )
