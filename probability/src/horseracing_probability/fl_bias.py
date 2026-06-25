"""Favorite-longshot (FL) bias correction of the market-implied win probability (Feature 013).

The market vote-share q_i=(1/odds_i)/Σ(1/odds_j) (Feature 010) carries favorite-longshot bias. We
calibrate q -> q' against realized 1st-place outcomes. CRITICAL (codex): the calibration is on the
RACE-NORMALIZED q'_i=g(q_i)/Σ_j g(q_j), NOT the per-horse marginal g(q_i) — renormalization changes
marginals. Canonical (and MVP-only) method is the power model q'∝q^γ, with γ fit by the normalized
conditional-logit winner likelihood (bounded 1-D MLE, deterministic). The produced q' is run through
the same engine normalize+clip so it is idempotent for the 009 engine (evaluated == used vector).

Leak boundary: q' is MARKET-derived, kept separate from model p (p≠q); odds/q/q' are NEVER a
win-model feature. Calibrators are fit train-only / walk-forward (strictly before the target race).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .engine import DEFAULT_EPS, _normalize_clip
from .market_odds import market_implied_win_probs

#: bounded gamma search interval for the power model (regularity: avoid degenerate extremes).
GAMMA_MIN = 0.1
GAMMA_MAX = 5.0
_FLAT_TOL = 1e-9  # q treated as non-informative (flat) when max-min below this


@dataclass(frozen=True)
class FLCalibrator:
    method: str                       # "power" (MVP). isotonic/loglog -> NotImplementedError.
    params: dict                      # power: {"gamma": float}
    train_window: tuple | None        # (date_from, date_to) of the fit window
    n_races: int                      # races seen in the fit window
    n_samples: int                    # INFORMATIVE races actually used for the MLE
    odds_range: tuple[float, float]   # (min q, max q) seen in training (extrapolation audit)
    logic_version: str
    sufficient: bool = True           # False -> identity fallback (gamma=1, no informative races)


@dataclass(frozen=True)
class CorrectedMarketProbs:
    race_id: str | None
    q: dict[str, float]               # raw market vote share (Σ=1)
    q_prime: dict[str, float]         # corrected, race-normalized, engine-consistent (Σ=1)
    field_size: int                   # corrected running set size
    excluded: list = field(default_factory=list)
    out_of_range: int = 0             # horses with q outside the training odds_range (audit)


# --- engine-consistent normalization ----------------------------------------
def _engine_normalize(probs: dict[str, float]) -> dict[str, float]:
    """Apply the 009 engine's renormalize->clip[eps,1-eps]->renormalize so q' is idempotent."""
    ids, p = _normalize_clip(probs, DEFAULT_EPS)
    return {ids[i]: p[i] for i in range(len(ids))}


def apply_g(method: str, params: dict, q: dict[str, float]) -> dict[str, float]:
    """Monotone g applied per horse then race-normalized + engine-consistent clip (idempotent)."""
    if method != "power":
        raise NotImplementedError(f"method '{method}' not implemented (power only in MVP)")
    if not q:
        raise ValueError("empty q")
    gamma = float(params["gamma"])
    g = {h: (qh ** gamma) for h, qh in q.items()}   # q∈(0,1], gamma>0 -> monotone
    s = sum(g.values())
    if s <= 0.0:
        raise ValueError("Σ g(q) <= 0")
    qp = {h: gv / s for h, gv in g.items()}
    return _engine_normalize(qp)                    # matches engine -> evaluated == used


# --- power gamma MLE (normalized conditional-logit winner likelihood) -------
def _valid_q(win_odds: dict[str, float]) -> dict[str, float]:
    valid = {h: float(o) for h, o in win_odds.items() if o is not None and float(o) > 0.0}
    if len(valid) < 2:
        return {}
    return market_implied_win_probs(valid)


def _informative(samples) -> list[tuple[dict[str, float], str]]:
    """(q, winner) for races with >=2 valid horses, a known winner in-field, and non-flat q."""
    out: list[tuple[dict[str, float], str]] = []
    for win_odds, winner in samples:
        if winner is None:
            continue
        q = _valid_q(win_odds)
        if not q or winner not in q:
            continue
        if max(q.values()) - min(q.values()) < _FLAT_TOL:
            continue  # flat q -> gradient in gamma is ~0 (non-informative)
        out.append((q, winner))
    return out


def _nll_gamma(gamma: float, races: list[tuple[dict[str, float], str]]) -> float:
    total = 0.0
    for q, winner in races:
        denom = sum(qh ** gamma for qh in q.values())
        num = q[winner] ** gamma
        total += -math.log(max(num / denom, DEFAULT_EPS))
    return total


def _golden_min(f, a: float, b: float, *, tol: float = 1e-6, max_iter: int = 200) -> float:
    """Deterministic golden-section minimization on [a, b] (no RNG, no seed)."""
    invphi = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - invphi * (b - a)
    d = a + invphi * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(max_iter):
        if (b - a) < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = f(d)
    return (a + b) / 2.0


def fit_power_gamma(samples) -> tuple[float, int]:
    """Return (gamma, n_informative). gamma minimizes normalized winner NLL on the gamma interval.

    Falls back to gamma=1 (identity) when there are no informative races.
    """
    races = _informative(samples)
    if not races:
        return 1.0, 0
    gamma = _golden_min(lambda g: _nll_gamma(g, races), GAMMA_MIN, GAMMA_MAX)
    return gamma, len(races)


# --- calibrator fit / apply -------------------------------------------------
def fit_fl_calibrator(
    samples,
    *,
    method: str = "power",
    select: str = "mle",
    min_samples: int = 20,
    train_window: tuple | None = None,
    version: str = "fl-0.1.0",
) -> FLCalibrator:
    """Fit an FL calibrator on past (win_odds, winner) samples (caller guarantees walk-forward)."""
    if method != "power":
        raise NotImplementedError(f"method '{method}' not implemented (power only in MVP)")
    gamma, n_info = fit_power_gamma(samples)
    # odds_range over informative q (extrapolation audit)
    qs = [qh for win_odds, _ in samples for qh in _valid_q(win_odds).values()]
    odds_range = (min(qs), max(qs)) if qs else (0.0, 1.0)
    logic_version = (
        f"fl=power(q^gamma);gamma={gamma:.5f};select={select};window={train_window};"
        f"n_races={len(samples)};n_info={n_info};v={version}"
    )
    return FLCalibrator(
        method="power", params={"gamma": gamma}, train_window=train_window,
        n_races=len(samples), n_samples=n_info, odds_range=odds_range,
        logic_version=logic_version, sufficient=(n_info >= 1),
    )


# --- walk-forward sample loader ---------------------------------------------
def race_before(a_date, a_id: str, b_date, b_id: str) -> bool:
    """Strictly-before by (race_date, race_id) lexicographic order (deterministic, time-free)."""
    return (a_date, a_id) < (b_date, b_id)


def load_samples(session, *, date_from, date_to):
    """Load (race_id, race_date, win_odds, winner|None) for races in [date_from, date_to].

    Ordered by (race_date, race_id) so any strictly-before split is deterministic. Caller
    guarantees the train window is strictly before the eval window (non-overlapping).
    """
    from horseracing_db.models import Race
    from sqlalchemy import select

    from .market_calibration import _race_winodds_and_winner  # lazy: avoid import cycle

    rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_date, Race.race_id)
    ).all()
    out = []
    for race_id, race_date in rows:
        win_odds, winner = _race_winodds_and_winner(session, race_id)
        out.append((race_id, race_date, win_odds, winner))
    return out


def apply_calibrator(
    calibrator: FLCalibrator, win_odds: dict[str, float], *, race_id: str | None = None
) -> CorrectedMarketProbs:
    """q -> q' (race-normalized, engine-consistent). Scratched/invalid excluded + renormalized."""
    excluded = [h for h, o in win_odds.items() if o is None or float(o) <= 0.0]
    valid = {h: float(o) for h, o in win_odds.items() if o is not None and float(o) > 0.0}
    if len(valid) < 2:
        q = market_implied_win_probs(valid) if valid else {}
        return CorrectedMarketProbs(race_id, q, dict(q), len(valid), excluded, 0)
    q = market_implied_win_probs(valid)
    lo, hi = calibrator.odds_range
    oor = sum(1 for qh in q.values() if qh < lo or qh > hi)  # extrapolation audit (power applies)
    q_prime = apply_g(calibrator.method, calibrator.params, q)
    return CorrectedMarketProbs(race_id, q, q_prime, len(valid), excluded, oor)
