"""ΔR² — does a model add information the MARKET does not already have? (Benter 1994)

Winner NLL answers "is this model accurate". It does NOT answer "can this model ever move ROI",
because in a pari-mutuel pool `ROI = E[c·t/q]`: the model enters only through WHICH bets get
selected, and a model whose information is already in `q` selects nothing the market hasn't
already priced. Benter's own instrument for that question is the pseudo-R² INCREMENT of a
two-stage model over the market alone:

    c_ri(α,β) ∝ p_ri^α · q_ri^β          (race-internal softmax over log p and log q)
    R²(s)     = 1 − D(s)/D₀,  D(s) = Σ_r −log s_r,winner,  D₀ = Σ_r log N_r

`D₀` is the uniform (1/N_r) null — McFadden's likelihood-ratio index, which is the definition
Bolton–Chapman (1986) used and Benter adopted. It must be `Σ log N_r` over the SAME eligible
races as every model being compared: `log(mean N)` or a mean of per-race R² are different (and
wrong) statistics.

Two increments are reported, and the difference between them matters:

* ``delta_r2_literal``       = R²(c) − R²(q)        — Benter's published quantity.
* ``delta_r2_model_given_market`` = R²(c) − R²(m)   where m_ri ∝ q_ri^γ is the market recalibrated
  by its OWN best power. This is PRIMARY. Without it, a market that merely wants γ≠1 (favourite–
  longshot correction) hands its own recalibration to the model's account, and `p` gets credit for
  information it never supplied.

Reference points from Benter (1994), 3,198 races / 32,877 starters:
``R²_public 0.1218 / R²_fundamental 0.1245 / R²_combined 0.1396`` → **ΔR² = 0.0178** for a model
that was profitably bet, versus **ΔR² = 0.0002** for an aggregate of 48 newspaper tipsters, which
he describes as producing no advantage bets at all.

Those numbers are REFERENCE LINES, not thresholds: they come from a different market, takeout,
field-size distribution and in-sample/OOS regime. This module is an evidence instrument, not an
adoption gate — see ``docs/plan/accuracy-roi-decoupling-investigation.md``.

In-sample ΔR² is structurally ≥ 0 (the reduced model is nested at α=0), so it says nothing on its
own. Coefficients are therefore fit PREQUENTIALLY: block k is scored with (α,β,γ) fit only on
strictly earlier blocks, cut at race-day granularity, and the first block is fit-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize

from .bootstrap import RatioBootstrapCI, race_day_cluster_ratio_bootstrap_ci_v1

#: Probability floor applied BEFORE the log, then renormalised. Fixed (not tuned): a raw 0 would
#: make the log infinite and let a single race dominate every aggregate.
EPS = 1e-15

#: A CI within +-this of zero is NOT evidence of a direction — float noise around exact
#: cancellation (p == q) must read as NO_DECISION, not as a significant effect.
ZERO_TOL = 1e-12

#: Benter (1994) reference lines — reported alongside, never compared against as a gate.
BENTER_FUNDAMENTAL_DELTA_R2 = 0.0178
BENTER_TIPSTER_DELTA_R2 = 0.0002


@dataclass(frozen=True)
class DeltaR2Race:
    """One eligible race: model probs, market probs, and who actually won.

    ``p`` and ``q`` must cover the SAME started field in the SAME order, with a single winner at
    ``winner_idx``. A race whose market is incomplete (any started horse without odds) does not
    belong here — dropping one horse and renormalising would compare `p` and `q` on different
    populations, which is exactly the mistake this instrument exists to avoid.
    """

    race_id: str
    day: str
    block: str            #: prequential block key (e.g. the year) — fit uses strictly earlier ones
    winner_idx: int
    p: np.ndarray
    q: np.ndarray
    subgroups: tuple[str, ...] = ()   #: outcome-INDEPENDENT race labels only


@dataclass(frozen=True)
class FitDiagnostics:
    block: str
    n_fit_races: int
    fit_through_day: str | None
    alpha: float
    beta: float
    gamma: float
    converged: bool
    n_iter: int
    message: str


@dataclass(frozen=True)
class DeltaR2Report:
    n_races: int
    n_days: int
    n_blocks_scored: int
    mean_log_field: float
    r2_model: float
    r2_market_raw: float
    r2_market_calibrated: float
    r2_combined: float
    delta_r2_literal: float
    delta_r2_model_given_market: float
    ci_literal: RatioBootstrapCI
    ci_model_given_market: RatioBootstrapCI
    verdict: str
    fits: tuple[FitDiagnostics, ...]
    n_floored: int
    reference: dict[str, float] = field(default_factory=lambda: {
        "benter_fundamental": BENTER_FUNDAMENTAL_DELTA_R2,
        "benter_tipster": BENTER_TIPSTER_DELTA_R2,
    })

    def to_dict(self) -> dict[str, Any]:
        def ci(c: RatioBootstrapCI) -> dict:
            return {"point": c.point, "ci_low": c.ci_low, "ci_high": c.ci_high,
                    "b": c.b, "seed": c.seed, "block": c.block, "n_days": c.n_days,
                    "no_decision": c.no_decision}
        return {
            "n_races": self.n_races, "n_days": self.n_days,
            "n_blocks_scored": self.n_blocks_scored,
            "mean_log_field": self.mean_log_field,
            "r2": {"model": self.r2_model, "market_raw": self.r2_market_raw,
                   "market_calibrated": self.r2_market_calibrated, "combined": self.r2_combined},
            "delta_r2_literal": self.delta_r2_literal,
            "delta_r2_model_given_market": self.delta_r2_model_given_market,
            "ci_literal": ci(self.ci_literal),
            "ci_model_given_market": ci(self.ci_model_given_market),
            "verdict": self.verdict,
            "n_floored": self.n_floored,
            "reference": dict(self.reference),
            "fits": [vars(f) for f in self.fits],
        }


# --- probability preprocessing ----------------------------------------------------------------

def _prepare(v: np.ndarray, *, what: str, race_id: str) -> tuple[np.ndarray, int]:
    """Validate, floor at EPS, renormalise. Returns (probs, n_floored). Fail-closed on garbage."""
    a = np.asarray(v, dtype=float)
    if a.ndim != 1 or a.size < 2:
        raise ValueError(f"race {race_id}: {what} must be a vector of >=2 probabilities")
    if not np.all(np.isfinite(a)):
        raise ValueError(f"race {race_id}: {what} contains a non-finite value")
    if np.any(a < 0) or np.any(a > 1 + 1e-9):
        raise ValueError(f"race {race_id}: {what} outside [0, 1]")
    n_floored = int(np.count_nonzero(a < EPS))
    a = np.maximum(a, EPS)
    s = a.sum()
    if not math.isfinite(s) or s <= 0:
        raise ValueError(f"race {race_id}: {what} does not sum to a positive value")
    return a / s, n_floored


def prepare_races(races: list[DeltaR2Race]) -> tuple[list[DeltaR2Race], int]:
    """Apply the identical preprocessing to p and q of every race (same floor, same order)."""
    out: list[DeltaR2Race] = []
    floored = 0
    for r in races:
        p, fp = _prepare(r.p, what="p", race_id=r.race_id)
        q, fq = _prepare(r.q, what="q", race_id=r.race_id)
        if p.size != q.size:
            raise ValueError(f"race {r.race_id}: p and q cover different fields")
        if not (0 <= r.winner_idx < p.size):
            raise ValueError(f"race {r.race_id}: winner_idx out of range")
        floored += fp + fq
        out.append(DeltaR2Race(r.race_id, r.day, r.block, r.winner_idx, p, q, r.subgroups))
    return out, floored


# --- the two-stage model -----------------------------------------------------------------------

def _log_arrays(races: list[DeltaR2Race]):
    return ([np.log(r.p) for r in races], [np.log(r.q) for r in races],
            [r.winner_idx for r in races])


def _mean_nll(coefs: np.ndarray, lp: list, lq: list, wi: list) -> float:
    """Mean winner NLL of ``softmax(α·log p + β·log q)`` — the conditional-logit loss.

    logsumexp is done by max-shift, so an extreme coefficient cannot overflow into inf/nan and
    silently look like a good fit.
    """
    a, b = float(coefs[0]), float(coefs[1])
    total = 0.0
    for x, y, w in zip(lp, lq, wi, strict=True):
        z = a * x + b * y
        m = z.max()
        total += float(m + np.log(np.exp(z - m).sum()) - z[w])
    return total / len(wi)


def _fit(lp, lq, wi, *, start, fix_alpha_zero: bool) -> tuple[float, float, bool, int, str]:
    """Unregularised MLE of (α, β), optionally with α pinned to 0 (market-only reduced model)."""
    if fix_alpha_zero:
        def obj(x):
            return _mean_nll(np.array([0.0, x[0]]), lp, lq, wi)
        res = minimize(obj, [start[1]], method="Nelder-Mead",
                       options={"xatol": 1e-7, "fatol": 1e-11, "maxiter": 2000})
        return 0.0, float(res.x[0]), bool(res.success), int(res.nit), str(res.message)

    def obj2(x):
        return _mean_nll(x, lp, lq, wi)
    res = minimize(obj2, list(start), method="Nelder-Mead",
                   options={"xatol": 1e-7, "fatol": 1e-11, "maxiter": 4000})
    return float(res.x[0]), float(res.x[1]), bool(res.success), int(res.nit), str(res.message)


def _race_nll(coefs: tuple[float, float], r: DeltaR2Race) -> float:
    a, b = coefs
    z = a * np.log(r.p) + b * np.log(r.q)
    m = z.max()
    return float(m + np.log(np.exp(z - m).sum()) - z[r.winner_idx])


# --- prequential evaluation --------------------------------------------------------------------

def evaluate_delta_r2(
    races: list[DeltaR2Race],
    *,
    b: int = 2000,
    seed: int = 20260729,
    alpha: float = 0.05,
    delta_min: float = 0.0,
) -> DeltaR2Report:
    """Prequential ΔR²: block k is scored with coefficients fit on strictly earlier blocks.

    The first block is fit-only (never scored) — it has no past to learn from. Blocks are cut at
    race-day granularity, so no race is scored by coefficients that saw a race on its own day.

    ``delta_min`` is the materiality threshold for ``material_positive``; the default 0.0 makes
    this an evidence-only instrument (any CI strictly above zero counts as evidence, nothing is
    declared materially useful without a separately justified δ_min).
    """
    races, n_floored = prepare_races(races)
    if not races:
        raise ValueError("no eligible races")

    blocks = sorted({r.block for r in races})
    scored: list[DeltaR2Race] = []
    nll_c: list[float] = []
    nll_m: list[float] = []
    nll_q: list[float] = []
    nll_p: list[float] = []
    fits: list[FitDiagnostics] = []

    for k, blk in enumerate(blocks):
        if k == 0:
            continue  # warm-up: nothing strictly earlier to fit on
        past = [r for r in races if r.block < blk]
        cur = [r for r in races if r.block == blk]
        if not past or not cur:
            continue
        # day-granular cut: coefficients must not have seen anything on/after the scored block
        fit_through = max(r.day for r in past)
        if fit_through >= min(r.day for r in cur):
            raise ValueError(f"block {blk}: fit window overlaps the scored window by day")

        lp, lq, wi = _log_arrays(past)
        a, bb, conv, nit, msg = _fit(lp, lq, wi, start=(1.0, 1.0), fix_alpha_zero=False)
        _, gamma, conv_g, _, _ = _fit(lp, lq, wi, start=(0.0, 1.0), fix_alpha_zero=True)
        fits.append(FitDiagnostics(blk, len(past), fit_through, a, bb, gamma,
                                   conv and conv_g, nit, msg))

        for r in cur:
            scored.append(r)
            nll_c.append(_race_nll((a, bb), r))
            nll_m.append(_race_nll((0.0, gamma), r))
            nll_q.append(_race_nll((0.0, 1.0), r))
            nll_p.append(_race_nll((1.0, 0.0), r))

    if not scored:
        raise ValueError("no scored races (need at least two blocks)")

    logn = np.array([math.log(r.p.size) for r in scored])
    d0 = float(logn.sum())
    r2 = lambda arr: 1.0 - float(np.sum(arr)) / d0  # noqa: E731 — local shorthand

    # ΔR² is a RATIO: the bootstrap must recompute the denominator from the resampled days too.
    num_lit: dict[str, list[float]] = {}
    num_cond: dict[str, list[float]] = {}
    den: dict[str, list[float]] = {}
    for r, c, m, qq, ln in zip(scored, nll_c, nll_m, nll_q, logn, strict=True):
        num_lit.setdefault(r.day, []).append(qq - c)
        num_cond.setdefault(r.day, []).append(m - c)
        den.setdefault(r.day, []).append(float(ln))

    ci_lit = race_day_cluster_ratio_bootstrap_ci_v1(num_lit, den, b=b, seed=seed, alpha=alpha)
    ci_cond = race_day_cluster_ratio_bootstrap_ci_v1(num_cond, den, b=b, seed=seed, alpha=alpha)

    if ci_cond.no_decision or ci_cond.ci_low is None or ci_cond.ci_high is None:
        verdict = "NO_DECISION"
    elif ci_cond.ci_high < -ZERO_TOL:
        verdict = "harmful"
    elif ci_cond.ci_low > max(delta_min, ZERO_TOL):
        verdict = "material_positive"
    elif ci_cond.ci_low > ZERO_TOL:
        verdict = "evidence_positive"
    else:
        verdict = "NO_DECISION"

    return DeltaR2Report(
        n_races=len(scored),
        n_days=len({r.day for r in scored}),
        n_blocks_scored=len(fits),
        mean_log_field=float(logn.mean()),
        r2_model=r2(nll_p),
        r2_market_raw=r2(nll_q),
        r2_market_calibrated=r2(nll_m),
        r2_combined=r2(nll_c),
        delta_r2_literal=ci_lit.point,
        delta_r2_model_given_market=ci_cond.point,
        ci_literal=ci_lit,
        ci_model_given_market=ci_cond,
        verdict=verdict,
        fits=tuple(fits),
        n_floored=n_floored,
    )


def subgroup_delta_r2(races: list[DeltaR2Race], report_races: list[DeltaR2Race] | None = None):
    """Placeholder kept intentionally unimplemented — see module docstring.

    Subgroups must be race-grain and outcome-INDEPENDENT (e.g. "2026 only", "field contains an
    nk: horse"). Slicing by winner identity would condition on the outcome and manufacture an
    effect. Left out of the first cut rather than shipped with a tempting-but-wrong default.
    """
    raise NotImplementedError("subgroup ΔR² is deferred; see module docstring")
