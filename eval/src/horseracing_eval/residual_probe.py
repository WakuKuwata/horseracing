"""Feature 081 Phase 0: residual-offset screening probe (pure, eval-side).

Codex's design: instead of re-training a full LightGBM per candidate feature, tilt the
ACTIVE model's out-of-fold race-softmax probability ``p`` by a small offset ``exp(gamma . h)``
and measure the marginal effect on winner NLL:

    p'[i,r] = p[i,r] * exp(gamma . h[i,r]) / sum_j p[j,r] * exp(gamma . h[j,r])

This preserves within-race sum-to-1 (constitution IV) and needs no booster refit. Two readouts:

* ``score_statistic`` U_r = h[winner,r] - sum_i p[i,r] h[i,r]  — the gamma=0 first-order score.
  mean(U) > 0 means a small positive gamma REDUCES winner NLL (the factor carries residual
  signal the active model has not used). This is the cheapest first pass and needs no fit.
* ``prequential_delta_nll`` — fit gamma on strictly-earlier folds, apply to the held-out fold,
  and record the per-race ΔNLL = NLL(p') - NLL(p). Aggregated ΔNLL (negative = improvement)
  is the pre-registered PRIMARY (``delta_winner_nll_probe``).

A race-CONSTANT h cancels in the softmax (U_r == 0 identically) — a built-in sanity check that
matches the ``race-constant-features-need-interaction`` finding: only within-race variation can
move a race-softmax ranking.

Leak boundary: ``p`` is the active model's strict-past OOF prediction and ``h`` is a strictly-
before / same-day-excluded candidate. This module is PURE — it never reads odds/results and never
writes anything back to model features (constitution II). Missing h (NaN) contributes 0 to the
exponent (multiplier 1 = no tilt), the honest neutral for a horse the factor cannot describe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RaceProbe:
    """One race's probe inputs on the canonical started field (winner included).

    ``p`` and ``h`` are aligned arrays over the started horses; ``winner_idx`` is the row of the
    single winner. ``h`` is (n_horses, k) for a k-column candidate; NaNs are treated as 0 (no
    tilt). ``p`` is renormalised within the race before use.
    """

    day: str
    p: np.ndarray            # (n,)
    h: np.ndarray            # (n, k)
    winner_idx: int


def _clean(race: RaceProbe) -> tuple[np.ndarray, np.ndarray]:
    """Return (p_renormalised (n,), h_filled (n,k)) with NaN h -> 0 and p summing to 1."""
    p = np.asarray(race.p, dtype=float)
    s = p.sum()
    p = p / s if s > 0 else np.full_like(p, 1.0 / len(p))
    h = np.asarray(race.h, dtype=float)
    if h.ndim == 1:
        h = h[:, None]
    h = np.where(np.isfinite(h), h, 0.0)
    return p, h


def score_statistic(race: RaceProbe) -> np.ndarray:
    """gamma=0 first-order score U_r = h[winner] - sum_i p_i h_i (per column, shape (k,))."""
    p, h = _clean(race)
    return h[race.winner_idx] - p @ h


def _delta_nll_race(p: np.ndarray, h: np.ndarray, winner_idx: int, gamma: np.ndarray) -> float:
    """ΔNLL_r(gamma) = NLL(p') - NLL(p) for one race. Convex in gamma (log-sum-exp)."""
    z = h @ gamma                       # (n,)
    z = z - z.max()                     # stabilise
    w = p * np.exp(z)
    denom = w.sum()
    # NLL(p') = -log p'[winner] = -log( p[winner] exp(z[winner]) / denom )
    # NLL(p)  = -log p[winner]
    # ΔNLL    = -( z[winner] - log denom )   (the p[winner] and normaliser of p cancel)
    return float(-(z[winner_idx] - np.log(denom)))


def fit_gamma(races: list[RaceProbe], *, k: int, max_iter: int = 50, tol: float = 1e-9,
              ridge: float = 1e-6) -> np.ndarray:
    """Fit gamma minimising mean ΔNLL over ``races`` by damped Newton (convex objective).

    ``ridge`` is a tiny L2 anchor to 0 that keeps the fit finite when a candidate perfectly
    separates a handful of races (screening prior: no tilt unless the data insists). Returns the
    zero vector when there is no within-race variation to fit.
    """
    gamma = np.zeros(k, dtype=float)
    cleaned = [(*_clean(r), r.winner_idx) for r in races]
    if not cleaned:
        return gamma
    for _ in range(max_iter):
        grad = np.zeros(k)
        hess = np.zeros((k, k))
        for p, h, wi in cleaned:
            z = h @ gamma
            z = z - z.max()
            w = p * np.exp(z)
            denom = w.sum()
            m = w / denom                      # softmax weights (n,)
            subgrad = h[wi] - m @ h            # -d(ΔNLL_r)/dgamma
            grad -= subgrad                    # d(ΔNLL)/dgamma
            hbar = m @ h                       # (k,)
            cov = (h * m[:, None]).T @ h - np.outer(hbar, hbar)   # (k,k) PSD
            hess += cov
        grad = grad / len(cleaned) + ridge * gamma
        hess = hess / len(cleaned) + ridge * np.eye(k)
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        gamma_new = gamma - step
        if not np.all(np.isfinite(gamma_new)):
            break
        if np.max(np.abs(gamma_new - gamma)) < tol:
            gamma = gamma_new
            break
        gamma = gamma_new
    return gamma


@dataclass(frozen=True)
class ProbeResult:
    candidate_id: str
    k: int
    n_races: int
    n_races_with_variation: int
    coverage: float                 # fraction of races with any within-race variation in h
    mean_score_U: list[float]       # gamma=0 first-order score per column
    delta_nll_by_day: dict          # day -> [per-race held-out ΔNLL]  (feeds bootstrap)
    point_delta_nll: float          # mean held-out ΔNLL (negative = improvement)
    gammas_by_fold: list[list[float]]


def _has_variation(race: RaceProbe) -> bool:
    _, h = _clean(race)
    return bool(np.any(np.ptp(h, axis=0) > 0))


def prequential_delta_nll(
    folds: list[list[RaceProbe]], candidate_id: str, k: int,
) -> ProbeResult:
    """Prequential probe: fit gamma on all strictly-earlier folds, apply to the held-out fold.

    ``folds`` is time-ordered; fold 0 has no prior data so it is skipped for the held-out ΔNLL
    (its gamma would be in-sample). The gamma=0 score U is computed over ALL races (it needs no
    fit and is the cheap direction indicator).
    """
    all_races = [r for f in folds for r in f]
    n = len(all_races)
    n_var = sum(_has_variation(r) for r in all_races)
    # gamma=0 score over all races
    if all_races:
        U = np.vstack([score_statistic(r) for r in all_races])   # (n, k)
        mean_U = U.mean(axis=0).tolist()
    else:
        mean_U = [0.0] * k

    delta_by_day: dict = {}
    gammas: list[list[float]] = []
    prior: list[RaceProbe] = list(folds[0]) if folds else []
    for fi in range(1, len(folds)):
        gamma = fit_gamma(prior, k=k)
        gammas.append(gamma.tolist())
        for r in folds[fi]:
            p, h = _clean(r)
            d = _delta_nll_race(p, h, r.winner_idx, gamma)
            delta_by_day.setdefault(r.day, []).append(d)
        prior.extend(folds[fi])

    held = [d for arr in delta_by_day.values() for d in arr]
    point = float(np.mean(held)) if held else float("nan")
    return ProbeResult(
        candidate_id=candidate_id, k=k, n_races=n, n_races_with_variation=n_var,
        coverage=(n_var / n) if n else 0.0, mean_score_U=mean_U,
        delta_nll_by_day=delta_by_day, point_delta_nll=point, gammas_by_fold=gammas,
    )
