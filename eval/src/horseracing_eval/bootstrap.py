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

import statistics
from dataclasses import dataclass, replace

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
    # An EMPTY cluster is counted as a day but contributes no rows, so a replicate that happens to
    # draw only empty days averages an empty array -> NaN -> a NaN percentile, i.e. a silently
    # undecidable CI reported as a number. Non-finite diffs poison the same way. Both indicate a
    # construction bug upstream, so they fail closed here rather than propagating (2026-08 review).
    for d, arr in zip(days, day_arrays, strict=True):
        if arr.size == 0:
            raise ValueError(f"race-day cluster {d!r} is empty; days must carry at least one race")
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"race-day cluster {d!r} contains non-finite paired differences")
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


@dataclass(frozen=True)
class RatioBootstrapCI:
    """CI of a RATIO statistic ``Σnum / Σden`` (e.g. ΔR² = Σ(ℓ_ref−ℓ_cand) / Σ log N)."""

    point: float
    ci_low: float | None
    ci_high: float | None
    b: int
    seed: int
    block: str
    n_days: int
    no_decision: bool
    #: the replicate ratios themselves — kept so a caller can form a one-sided bootstrap p-value
    #: (e.g. P(ROI <= 1)) without re-running the resampling with a second, drifting seed.
    replicates: tuple[float, ...] = ()


def race_day_cluster_ratio_bootstrap_ci_v1(
    num_by_day: dict,
    den_by_day: dict,
    *,
    b: int = 2000,
    seed: int = 20260712,
    alpha: float = 0.05,
) -> RatioBootstrapCI:
    """95% percentile CI of ``Σnum / Σden`` via race-day cluster bootstrap.

    ΔR² is a RATIO, not a mean, so each replicate must recompute BOTH sums from the resampled
    days. Holding the denominator fixed at its original value (or averaging per-race ratios)
    understates the variance, because field sizes co-vary with the days that get resampled.

    ``num_by_day``/``den_by_day`` map the same race-day keys to per-race numerator/denominator
    contributions; the two must align race-for-race within each day. Determinism and the
    NO_DECISION rule (< 2 days) match ``race_day_cluster_bootstrap_ci_v1``.
    """
    days = sorted(num_by_day.keys())
    if sorted(den_by_day.keys()) != days:
        raise ValueError("numerator and denominator must cover the same race-days")
    nums = [np.asarray(num_by_day[d], dtype=float) for d in days]
    dens = [np.asarray(den_by_day[d], dtype=float) for d in days]
    for d, (n, x) in zip(days, zip(nums, dens, strict=True), strict=True):
        if n.shape != x.shape:
            raise ValueError(f"day {d}: numerator/denominator race counts differ")
    n_days = len(days)
    tot_n = float(sum(float(a.sum()) for a in nums))
    tot_d = float(sum(float(a.sum()) for a in dens))
    point = tot_n / tot_d if tot_d > 0 else float("nan")

    if n_days < 2:
        return RatioBootstrapCI(point, None, None, b, seed, "race_day", n_days, no_decision=True)

    rng = np.random.default_rng(seed)
    boots = np.empty(b, dtype=float)
    for i in range(b):
        pick = rng.integers(0, n_days, size=n_days)
        sn = sum(float(nums[j].sum()) for j in pick)
        sd = sum(float(dens[j].sum()) for j in pick)
        boots[i] = sn / sd if sd > 0 else np.nan
    ci_low = float(np.percentile(boots, 100.0 * alpha / 2.0))
    ci_high = float(np.percentile(boots, 100.0 * (1.0 - alpha / 2.0)))
    return RatioBootstrapCI(point, ci_low, ci_high, b, seed, "race_day", n_days,
                            no_decision=False, replicates=tuple(float(x) for x in boots))


# ---------------------------------------------------------------------------------------------
# Retraining noise (2026-08-18).
#
# The cluster bootstrap resamples RACES. It cannot see the variation that comes from refitting the
# model: measured by re-running the same two-arm comparison with only the seed changed, the diff
# moved with SD 0.001816 at fold level, against a same-fold bootstrap SE of 0.002239 — a ratio of
# 0.81. Because that component is missing, the reported interval was ~20% too narrow on a standard
# window and the gate's effective false-positive rate was 5.8%, not the nominal 2.5%.
#
# Averaging k seeds is a bad trade and does NOT fix it: the race sample is identical across those
# runs, so only the seed component shrinks and the total can never fall below the sampling SE
# (k=3 buys an 8% correction for 3x the compute; k->inf still leaves the 20%-understated interval
# unchanged in the limit of what it can reach). The fix is to REPORT the missing variance, not to
# average it away.
# ---------------------------------------------------------------------------------------------


def seed_noise_sd(sd_fold: float, *, n_folds: int, k_seeds: int = 1) -> float:
    """Window-level retraining SD from the fold-level measurement.

    Each outer fold is a separate fit, so its seed perturbation is an independent draw and the
    window mean averages them: ``sd_fold / sqrt(n_folds)``. Running the whole comparison ``k``
    times and averaging shrinks it by a further ``sqrt(k)``.

    The fold-independence assumption is NOT measured — it is the reason this is a declared,
    frozen input rather than something the harness infers.

    ``k_seeds`` MODELS REPEATING THE WHOLE COMPARISON. IT IS NOT AN ENSEMBLE TERM.
    -----------------------------------------------------------------------------
    Dividing by ``sqrt(k)`` assumes the k draws are independent. Re-running the comparison end to
    end with a different seed satisfies that; averaging k boosters inside one shipped model does
    NOT, because those members share a training set. For a within-member correlation ``rho`` the
    variance of the average is

        Var(mean) = sigma^2 * (rho + (1 - rho) / k)

    and the ``rho * sigma^2`` part survives however large ``k`` gets. Feeding an ensemble's k into
    this function reports a shrinkage that was never achieved, in the direction that makes the
    interval too narrow — i.e. the failure mode nobody notices, because a narrower interval reads
    as a better result.

    So a k-seed ensemble (feature 100 US3) must NOT reach this function. Its shrinkage has to be
    measured from independent k-seed bundles compared bundle against bundle, or not claimed at all
    (FR-026b). ``eval/tests/unit/test_seed_noise_contract.py`` pins this.
    """
    if sd_fold <= 0 or n_folds < 1 or k_seeds < 1:
        return 0.0
    return float(sd_fold) / (float(n_folds) ** 0.5 * float(k_seeds) ** 0.5)


def inflate_for_seed_noise(
    ci: BootstrapCI, *, sd_fold: float, n_folds: int, k_seeds: int = 1, alpha: float = 0.05
) -> BootstrapCI:
    """Widen a sampling-only interval by the independent retraining component.

    Variances add, so each ARM is widened separately (percentile intervals are not symmetric and
    the asymmetry carries real information about the estimate). Returns the interval unchanged
    when there is nothing to add or no interval to widen.
    """
    sd = seed_noise_sd(sd_fold, n_folds=n_folds, k_seeds=k_seeds)
    if sd <= 0 or ci.ci_low is None or ci.ci_high is None:
        return ci
    z = statistics.NormalDist().inv_cdf(1.0 - alpha / 2.0)
    pad = z * sd
    lo_arm = ci.point - ci.ci_low
    hi_arm = ci.ci_high - ci.point
    return replace(
        ci,
        ci_low=ci.point - float(np.hypot(lo_arm, pad)),
        ci_high=ci.point + float(np.hypot(hi_arm, pad)),
    )


# ---------------------------------------------------------------------------------------------
# Feature 109: vectorised block ratio bootstrap (buy-pattern gate).
#
# The buy-pattern gate scores ~400 patterns x 20,000 replicates per window. The per-replicate
# Python loop above is fine for one series but not for that volume, so this variant collapses each
# series to per-BLOCK sums once, draws every replicate's block multiset in one call, and reduces
# with a single matrix product. With ``block="race_day"`` it reproduces
# ``race_day_cluster_ratio_bootstrap_ci_v1`` for the same seed up to float summation order
# (contract test: 1e-12), and it is the ONLY bootstrap implementation feature 109 uses — the
# recompute path calls this same function so evidence and verdict agree bit-for-bit.
#
# Blocks are FIXED clusters (a race-day, an ISO week, a calendar month), not moving blocks.
# ---------------------------------------------------------------------------------------------

_BLOCK_KINDS = ("race_day", "iso_week", "calendar_month")
_COUNTS_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def block_keys_for(day_keys, block: str) -> list:
    """Map sorted ``YYYY-MM-DD`` day keys to fixed-cluster block keys."""
    import datetime as _dt

    if block not in _BLOCK_KINDS:
        raise ValueError(f"unknown block kind {block!r}; expected one of {_BLOCK_KINDS}")
    out = []
    for d in day_keys:
        s = str(d)[:10]
        if block == "race_day":
            out.append(s)
            continue
        dt = _dt.date.fromisoformat(s)
        if block == "iso_week":
            iso = dt.isocalendar()
            out.append((int(iso[0]), int(iso[1])))
        else:
            out.append((dt.year, dt.month))
    return out


def block_bootstrap_counts(n_blocks: int, b: int, seed: int) -> np.ndarray:
    """``(b, n_blocks)`` multiset counts of a with-replacement block draw, cached per (n, b, seed).

    Drawing ``rng.integers(0, n, size=(b, n))`` in one call yields exactly the same stream as ``b``
    sequential ``rng.integers(0, n, size=n)`` calls (verified on numpy's Generator), which is what
    keeps this reproducible against the per-series loop version for the same seed.
    """
    key = (int(n_blocks), int(b), int(seed))
    hit = _COUNTS_CACHE.get(key)
    if hit is not None:
        return hit
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, n_blocks, size=(b, n_blocks))
    counts = np.zeros((b, n_blocks), dtype=np.int32)
    for i in range(b):
        counts[i] = np.bincount(picks[i], minlength=n_blocks)
    _COUNTS_CACHE[key] = counts
    return counts


@dataclass(frozen=True)
class BlockRatioBootstrap:
    """Vectorised ``Σnum/Σden`` block bootstrap for P series sharing one day axis."""

    block: str
    b: int
    seed: int
    n_blocks: int
    n_days: int
    no_decision: bool
    point: np.ndarray        #: (P,)
    ci_low: np.ndarray       #: (P,) NaN when undecidable
    ci_high: np.ndarray      #: (P,)
    replicates: np.ndarray   #: (P, b) NaN where the resampled denominator is zero
    n_zero_den: np.ndarray   #: (P,) how many replicates had a zero denominator

    def row(self, i: int) -> RatioBootstrapCI:
        nd = self.no_decision or not np.isfinite(self.ci_low[i])
        return RatioBootstrapCI(
            float(self.point[i]),
            None if nd else float(self.ci_low[i]),
            None if nd else float(self.ci_high[i]),
            self.b, self.seed, self.block, self.n_days, no_decision=bool(nd),
            replicates=tuple(float(x) for x in self.replicates[i]),
        )


def race_block_ratio_bootstrap_ci_v1(
    num,
    den,
    day_keys,
    *,
    block: str = "race_day",
    b: int = 20000,
    seed: int = 20260905,
    alpha: float = 0.05,
) -> BlockRatioBootstrap:
    """Percentile CI of ``Σnum/Σden`` per row via a fixed-cluster block bootstrap.

    ``num``/``den`` are ``(P, D)`` (a 1-D input is one row); column ``j`` is the per-day total for
    ``day_keys[j]``. ``day_keys`` must be sorted ascending ``YYYY-MM-DD`` strings and cover the
    whole window (days with no bet are zero columns — they still belong to the block universe).
    Every row shares the same block draws (synchronised resampling), which is what a Holm
    step-down over several patterns needs.
    """
    num2 = np.atleast_2d(np.asarray(num, dtype=float))
    den2 = np.atleast_2d(np.asarray(den, dtype=float))
    if num2.shape != den2.shape:
        raise ValueError("num and den must have the same shape")
    days = [str(d)[:10] for d in day_keys]
    if num2.shape[1] != len(days):
        raise ValueError("second axis must match day_keys")
    if days != sorted(days):
        raise ValueError("day_keys must be sorted ascending")
    if len(set(days)) != len(days):
        raise ValueError("day_keys must be unique")
    if not (np.all(np.isfinite(num2)) and np.all(np.isfinite(den2))):
        raise ValueError("num/den must be finite")

    keys = block_keys_for(days, block)
    uniq: list = []
    g = np.empty(len(days), dtype=np.int64)
    index: dict = {}
    for j, k in enumerate(keys):
        if k not in index:
            index[k] = len(uniq)
            uniq.append(k)
        g[j] = index[k]
    n_blocks = len(uniq)
    onehot = np.zeros((len(days), n_blocks), dtype=float)
    onehot[np.arange(len(days)), g] = 1.0
    num_b = num2 @ onehot
    den_b = den2 @ onehot

    tot_n = num2.sum(axis=1)
    tot_d = den2.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        point = np.where(tot_d > 0, tot_n / tot_d, np.nan)
    p = num2.shape[0]
    if n_blocks < 2:
        nan = np.full(p, np.nan)
        return BlockRatioBootstrap(block, b, seed, n_blocks, len(days), True, point, nan, nan,
                                   np.full((p, b), np.nan), np.full(p, b, dtype=np.int64))

    counts_t = block_bootstrap_counts(n_blocks, b, seed).astype(float).T   # (n_blocks, b)
    # Row-by-row gemv (not one gemm): a BLAS gemm changes its blocking with the row count, so a
    # 1-row call and a 393-row call would differ in the last bit. Per-row products make a row's
    # replicates independent of how many other rows were scored alongside it (recompute parity).
    rn = np.empty((p := num2.shape[0], b))
    rd = np.empty((p, b))
    for i in range(p):
        rn[i] = num_b[i] @ counts_t
        rd[i] = den_b[i] @ counts_t
    with np.errstate(divide="ignore", invalid="ignore"):
        reps = np.where(rd > 0, rn / rd, np.nan)
    n_zero = (rd <= 0).sum(axis=1)
    lo = np.full(p, np.nan)
    hi = np.full(p, np.nan)
    has = np.isfinite(reps).any(axis=1)
    if has.any():
        lo[has] = np.nanpercentile(reps[has], 100.0 * alpha / 2.0, axis=1)
        hi[has] = np.nanpercentile(reps[has], 100.0 * (1.0 - alpha / 2.0), axis=1)
    return BlockRatioBootstrap(block, b, seed, n_blocks, len(days), False, point, lo, hi, reps,
                               n_zero.astype(np.int64))
