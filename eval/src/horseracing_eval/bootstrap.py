"""Race-day CLUSTER bootstrap for paired loss-difference CIs (Feature 068 FR-004; renamed in 073).

The statistic is the overall mean paired difference ``candidate_loss - active_loss`` over all
races. Resampling is at the RACE-DAY granularity: each replicate independently resamples whole
race-days (block length = 1 day) with replacement and pools their races, so every race on a
resampled day moves together, preserving intra-day correlation (same track/going/bias). i.i.d.
race shuffling is FORBIDDEN — it would treat correlated same-day races as independent and
understate the CI (research D2/D4).

Feature 073 (US3, FR-013): the canonical name is ``race_day_cluster_bootstrap_ci_v1`` — the
implementation is a block-length-1 *cluster* bootstrap over days, NOT a moving-block bootstrap
(the old name was a misnomer). The numbers are byte-identical to the pre-073 function; only the
name changed. v2 block-width sensitivities (2/3/4 days, week, meeting) are diagnostic-only.

Determinism: a fixed integer ``seed`` drives ``numpy.random.default_rng`` so two runs with the
same seed produce bit-identical CIs (SC-002/SC-003). Fewer than 2 race-days → CI None (NO_DECISION).
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


def race_day_cluster_bootstrap_ci_v1(
    diffs_by_day: dict,
    *,
    b: int = 2000,
    seed: int = 20260712,
    alpha: float = 0.05,
) -> BootstrapCI:
    """95% percentile CI of the mean paired diff via race-day cluster bootstrap (v1).

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


def _rebucket_consecutive(diffs_by_day: dict, width: int) -> dict:
    """Group sorted race-days into consecutive blocks of ``width`` days (coarser cluster unit)."""
    days = sorted(diffs_by_day)
    blocks: dict = {}
    for i, d in enumerate(days):
        blocks.setdefault(f"blk{i // width}", []).extend(diffs_by_day[d])
    return blocks


def _rebucket_week(diffs_by_day: dict) -> dict:
    """Group race-days by ISO calendar week (day keys are ISO ``YYYY-MM-DD`` strings)."""
    from datetime import date
    blocks: dict = {}
    for d, vals in diffs_by_day.items():
        y, w, _ = date.fromisoformat(d).isocalendar()
        blocks.setdefault(f"{y}-W{w:02d}", []).extend(vals)
    return blocks


def race_day_cluster_bootstrap_sensitivity_v2(
    diffs_by_day: dict,
    *,
    widths: tuple[int, ...] = (2, 3, 4),
    include_week: bool = True,
    b: int = 2000,
    seed: int = 20260713,
    alpha: float = 0.05,
) -> dict[str, BootstrapCI]:
    """Feature 073 (US3, FR-014): DIAGNOSTIC block-width sensitivities of the primary CI.

    Re-buckets the day-keyed diffs into coarser blocks (``2d``/``3d``/``4d`` consecutive days,
    ``week`` = ISO week) and reuses the same cluster bootstrap on each. These are diagnostic only —
    they are NEVER ANDed into the adoption gate (the primary estimator remains
    ``race_day_cluster_bootstrap_ci_v1``). ``meeting`` (venue meeting) is intentionally omitted
    here because the day-keyed input carries no venue; compute it upstream if a venue key exists.
    """
    out: dict[str, BootstrapCI] = {}
    for w in widths:
        out[f"{w}d"] = race_day_cluster_bootstrap_ci_v1(
            _rebucket_consecutive(diffs_by_day, w), b=b, seed=seed, alpha=alpha
        )
    if include_week:
        out["week"] = race_day_cluster_bootstrap_ci_v1(
            _rebucket_week(diffs_by_day), b=b, seed=seed, alpha=alpha
        )
    return out
