"""Race-day moving-block bootstrap for paired loss-difference CIs (Feature 068, FR-004).

The statistic is the overall mean paired difference ``candidate_loss - active_loss`` over
all races. Resampling is at the RACE-DAY granularity (block = one race-day, moving-block of
length 1 day): every race on a resampled day moves together, preserving intra-day
correlation (same track/going/bias). i.i.d. race shuffling is FORBIDDEN — it would treat
correlated same-day races as independent and understate the CI (research D2).

Determinism: a fixed integer ``seed`` drives ``numpy.random.default_rng`` so two runs with the
same seed produce bit-identical CIs (SC-002). Fewer than 2 race-days → ``NO_DECISION`` (CI None).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BootstrapCI:
    point: float          #: overall mean paired diff (all races)
    ci_low: float | None  #: None when NO_DECISION (too few days)
    ci_high: float | None
    b: int
    seed: int
    block: str            #: "race_day"
    n_days: int
    no_decision: bool


def moving_block_bootstrap_ci(
    diffs_by_day: dict,
    *,
    b: int = 2000,
    seed: int = 20260712,
    alpha: float = 0.05,
) -> BootstrapCI:
    """95% percentile CI of the mean paired diff via race-day moving-block bootstrap.

    ``diffs_by_day`` maps a race-day key to the list of per-race paired diffs on that day.
    Days are sorted for determinism; each bootstrap replicate resamples ``n_days`` day-blocks
    with replacement and pools their races. With < 2 days the CI is undefined → NO_DECISION.
    """
    days = sorted(diffs_by_day.keys())
    day_arrays = [np.asarray(diffs_by_day[d], dtype=float) for d in days]
    n_days = len(days)
    all_diffs = np.concatenate(day_arrays) if day_arrays else np.asarray([], dtype=float)
    point = float(all_diffs.mean()) if all_diffs.size else float("nan")

    if n_days < 2:
        return BootstrapCI(point, None, None, b, seed, "race_day", n_days, no_decision=True)

    rng = np.random.default_rng(seed)
    boots = np.empty(b, dtype=float)
    for i in range(b):
        pick = rng.integers(0, n_days, size=n_days)
        sample = np.concatenate([day_arrays[j] for j in pick])
        boots[i] = sample.mean()
    ci_low = float(np.percentile(boots, 100.0 * alpha / 2.0))
    ci_high = float(np.percentile(boots, 100.0 * (1.0 - alpha / 2.0)))
    return BootstrapCI(point, ci_low, ci_high, b, seed, "race_day", n_days, no_decision=False)
