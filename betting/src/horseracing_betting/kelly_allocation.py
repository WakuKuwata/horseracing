"""Allocate Kelly stakes across mutually-exclusive bets in one (race, bet_type) (research.md R2).

Within a single bet type at most ONE selection wins, so the bets are NOT independent — a winning
selection means every other selection in the group loses. codex flagged that summing independent f*
over-bets. The canonical method maximizes expected log growth jointly:

    G(f) = Σ_c P_c·log(1 − S + O_c·f_c) + (1 − ΣP_c)·log(1 − S),   S = Σ_c f_c

G is concave, so a deterministic projected-gradient ascent finds the unique full-Kelly allocation.
Fractional λ (per odds source) and the caps are applied AFTER the joint optimum. The `heuristic`
mode instead sizes each bet independently (f* per bet) — its over-bet vs the exact optimum is what
the backtest measures (FR-004). Nothing here reads results or model features (leak boundary).
"""

from __future__ import annotations

from .kelly_types import KellyConfig

_EPS = 1e-12


def _project_capped(x: list[float], cap: float, total: float) -> list[float]:
    """Euclidean projection onto {0 ≤ f_i ≤ cap, Σ f_i ≤ total} (deterministic bisection)."""
    clipped = [min(max(v, 0.0), cap) for v in x]
    if sum(clipped) <= total + _EPS:
        return clipped
    # Σ exceeds budget: find τ with Σ clip(x_i − τ, 0, cap) = total.
    lo, hi = (min(x) - total), max(x)
    for _ in range(100):
        tau = (lo + hi) / 2.0
        s = sum(min(max(v - tau, 0.0), cap) for v in x)
        if s > total:
            lo = tau
        else:
            hi = tau
    return [min(max(v - hi, 0.0), cap) for v in x]


def _log_growth(p: list[float], o: list[float], f: list[float]) -> float:
    s = sum(f)
    w0 = 1.0 - s
    if w0 <= 0.0:
        return float("-inf")
    p0 = 1.0 - sum(p)
    g = p0 * _ln(w0)
    for c in range(len(f)):
        wc = 1.0 - s + o[c] * f[c]
        if wc <= 0.0:
            return float("-inf")
        g += p[c] * _ln(wc)
    return g


def _ln(x: float) -> float:
    from math import log

    return log(x)


def maximize_log_growth(
    p: list[float], o: list[float], *, cap_bet: float = 1.0, cap_total: float = 1.0,
    iters: int = 200,
) -> list[float]:
    """Full-Kelly allocation maximizing expected log growth (deterministic projected gradient)."""
    n = len(p)
    if n == 0:
        return []
    f = [0.0] * n  # feasible start (no bet)
    for _ in range(iters):
        s = sum(f)
        w0 = 1.0 - s
        p0 = 1.0 - sum(p)
        wc = [1.0 - s + o[c] * f[c] for c in range(n)]
        if w0 <= 0.0 or any(w <= 0.0 for w in wc):
            break
        t = p0 / w0 + sum(p[c] / wc[c] for c in range(n))
        grad = [p[c] * o[c] / wc[c] - t for c in range(n)]
        cur = _log_growth(p, o, f)
        step = 1.0
        improved = False
        while step > 1e-10:
            cand = _project_capped([f[c] + step * grad[c] for c in range(n)], cap_bet, cap_total)
            if _log_growth(p, o, cand) > cur + 1e-12:
                f = cand
                improved = True
                break
            step /= 2.0
        if not improved:  # converged (no ascent direction within the feasible set)
            break
    return f


def allocate_kelly(
    group: list[tuple[float, float, bool, float]],
    *,
    cfg: KellyConfig,
) -> list[float]:
    """Stake fractions for one (race, bet_type) group.

    ``group`` items are (p_model, odds_used, is_estimated, raw_f). Returns aligned stake_fraction
    list (0 for dropped bets). `exact` maximizes joint log growth then applies per-bet λ and caps;
    `heuristic` sizes each bet independently (λ·f*) then scales to the total cap.
    """
    if not group:
        return []
    p = [g[0] for g in group]
    o = [g[1] for g in group]
    lam = [cfg.lam(is_estimated=g[2]) for g in group]
    raw = [g[3] for g in group]

    if cfg.allocation == "exact":
        # full-Kelly joint optimum (budget = whole bankroll), then fractional λ per bet.
        full = maximize_log_growth(p, o, cap_bet=1.0, cap_total=1.0)
        scaled = [lam[i] * full[i] for i in range(len(group))]
    elif cfg.allocation == "heuristic":
        scaled = [lam[i] * raw[i] for i in range(len(group))]
    else:
        raise ValueError(f"unknown allocation: {cfg.allocation}")

    # per-bet cap, then total cap (proportional scale-down preserving the per-bet cap).
    capped = [min(v, cfg.cap_bet) for v in scaled]
    total = sum(capped)
    if total > cfg.cap_total + _EPS and total > 0.0:
        capped = _project_capped(capped, cfg.cap_bet, cfg.cap_total)
    return capped
