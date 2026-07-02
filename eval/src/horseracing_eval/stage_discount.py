"""Stage-discounted Plackett-Luce/Harville derivation + λ fitting (Feature 049).

The plain Harville tail (``baselines.harville_topk``) systematically overestimates
strong horses' P(top2)/P(top3) (Henery 1981 / Stern 1990 / Benter's operational
correction; confirmed on our own OOS reliability bins — top3 band 0.8-0.9 predicts
0.842 vs realized 0.746). This module implements the Benter-style fix: the stage-2/3
sequential conditionals use discounted weights p^λ_j instead of p, with λ_2/λ_3 fit
by conditional NLL on observed 2nd/3rd finishers. Stage 1 is NEVER touched (λ_1=1),
so win probabilities are byte-identical by construction.

Contract: specs/049-harville-stage-discount/contracts/stage-discount.md (INV-S1..S9).
The golden-section minimizer mirrors ``horseracing_probability.fl_bias._golden_min``
(013/017/048) but is re-implemented locally because the dependency direction is
probability -> eval — eval cannot import probability (plan Complexity Tracking).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-12  # same remaining-mass guard as baselines.harville_topk

LAMBDA_MIN = 0.1
LAMBDA_MAX = 5.0
DEFAULT_MIN_RACES = 300
#: a fitted λ this close to the search boundary is treated as misspecified -> identity
_BOUNDARY_TOL = 1e-3

_INV_PHI = (math.sqrt(5.0) - 1.0) / 2.0


@dataclass(frozen=True)
class StageDiscount:
    """Stage-2/3 power discount (data-model.md). lambda2=lambda3=1.0 == plain Harville."""

    lambda2: float = 1.0
    lambda3: float = 1.0
    n_races_l2: int = 0
    n_races_l3: int = 0
    fallback: bool = False

    @property
    def is_identity(self) -> bool:
        return self.lambda2 == 1.0 and self.lambda3 == 1.0


IDENTITY = StageDiscount()


def _golden_min(f, a: float, b: float, *, tol: float = 1e-6, max_iter: int = 200) -> float:
    """Deterministic golden-section minimum of unimodal f on [a, b] (fl_bias 同型)."""
    c = b - _INV_PHI * (b - a)
    d = a + _INV_PHI * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(max_iter):
        if abs(b - a) < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - _INV_PHI * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + _INV_PHI * (b - a)
            fd = f(d)
    return (a + b) / 2.0


def discounted_topk(
    win: list[float], sd: StageDiscount
) -> tuple[list[float], list[float]]:
    """P(top2)/P(top3) with stage-discounted sequential conditionals (contract formulas).

    win must be the race-normalized vector (Σ≈1, clipped). Identity sd delegates to the
    caller's plain path — callers wanting byte-parity should branch BEFORE calling this
    (``baselines.harville_topk`` does); this function itself is exact for λ=1 up to
    floating pow, hence the explicit branch lives at the call sites.
    """
    n = len(win)
    w2 = [p ** sd.lambda2 for p in win]
    w3 = [p ** sd.lambda3 for p in win]
    s2 = sum(w2)
    s3 = sum(w3)
    top2 = [0.0] * n
    top3 = [0.0] * n
    for i in range(n):
        t2 = win[i]
        for j in range(n):
            if j == i:
                continue
            dj = s2 - w2[j]
            if dj > _EPS:
                t2 += win[j] * w2[i] / dj
        top2[i] = min(t2, 1.0)

        t3 = top2[i]
        for j in range(n):
            if j == i:
                continue
            dj = s2 - w2[j]
            if dj <= _EPS:
                continue
            for k in range(n):
                if k == i or k == j:
                    continue
                djk = s3 - w3[j] - w3[k]
                if djk <= _EPS:
                    continue
                t3 += win[j] * (w2[k] / dj) * (w3[i] / djk)
        top3[i] = min(max(t3, top2[i]), 1.0)
    return top2, top3


def _nll_stage2(lam: float, races) -> float:
    """−Σ log[ w2(2nd) / (S2 − w2(winner)) ] over races with unique 1st+2nd."""
    nll = 0.0
    for win, i1, i2 in races:
        w = [p ** lam for p in win]
        denom = sum(w) - w[i1]
        if denom <= _EPS or w[i2] <= 0.0:
            nll += 700.0  # degenerate race under this λ; harshly penalized, deterministic
            continue
        nll -= math.log(w[i2] / denom)
    return nll


def _nll_stage3(lam: float, races) -> float:
    """−Σ log[ w3(3rd) / (S3 − w3(1st) − w3(2nd)) ] over races with unique 1st..3rd."""
    nll = 0.0
    for win, i1, i2, i3 in races:
        w = [p ** lam for p in win]
        denom = sum(w) - w[i1] - w[i2]
        if denom <= _EPS or w[i3] <= 0.0:
            nll += 700.0
            continue
        nll -= math.log(w[i3] / denom)
    return nll


@dataclass(frozen=True)
class TopkSample:
    """One race's fitting sample: normalized win vector + index of finishers 1..3.

    ``i2``/``i3`` are None when that finishing position is missing or non-unique
    (dead heat) — the race then contributes only to the stages it can label.
    Results are used ONLY as fit labels here (never selection/features, 憲法 II).
    """

    win: tuple[float, ...]
    i1: int | None
    i2: int | None
    i3: int | None


def fit_stage_discount(
    samples: list[TopkSample], *, min_races: int = DEFAULT_MIN_RACES
) -> StageDiscount:
    """Fit λ_2/λ_3 independently by conditional NLL (contract; deterministic).

    Insufficient samples (< min_races per stage) or a boundary-stuck optimum fall
    back to identity for that feature-level decision: per spec, EITHER stage failing
    makes the whole discount identity (fallback=True) — a half-discounted engine is
    harder to audit than none.
    """
    races2 = [
        (list(s.win), s.i1, s.i2)
        for s in samples
        if s.i1 is not None and s.i2 is not None
    ]
    races3 = [
        (list(s.win), s.i1, s.i2, s.i3)
        for s in samples
        if s.i1 is not None and s.i2 is not None and s.i3 is not None
    ]
    n2, n3 = len(races2), len(races3)
    if n2 < min_races or n3 < min_races:
        return StageDiscount(n_races_l2=n2, n_races_l3=n3, fallback=True)

    lam2 = _golden_min(lambda g: _nll_stage2(g, races2), LAMBDA_MIN, LAMBDA_MAX)
    lam3 = _golden_min(lambda g: _nll_stage3(g, races3), LAMBDA_MIN, LAMBDA_MAX)
    for lam in (lam2, lam3):
        if lam - LAMBDA_MIN < _BOUNDARY_TOL or LAMBDA_MAX - lam < _BOUNDARY_TOL:
            return StageDiscount(n_races_l2=n2, n_races_l3=n3, fallback=True)
    return StageDiscount(lambda2=lam2, lambda3=lam3, n_races_l2=n2, n_races_l3=n3)


def logic_version_fragment(sd: StageDiscount | None) -> str:
    """Audit fragment for logic_version (data-model.md). None -> no fragment (caller omits)."""
    if sd is None:
        return ""
    if sd.is_identity:
        return "sdisc=identity"
    return (
        f"sdisc=harville;l2={sd.lambda2:.5f};l3={sd.lambda3:.5f}"
        f";n2={sd.n_races_l2};n3={sd.n_races_l3}"
    )
