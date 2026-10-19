"""Feature 141: market-mixed expected return (期待回収率・市場混合) — the shared definition.

The displayed expected return of the market-aware model (``mev-ens15-v1``: 15-seed mean p̄ × win
odds) is over-stated on the horses it selects (winner's curse): on the closing-odds walk-forward
(2013..2026-06) Σwon/Σp̄ is 0.91 for EV 1.0–1.2, 0.78 for 1.2–1.5 and 0.57 above 1.5. A
one-dimensional recalibration of p̄ does not fix it; mixing with the market does::

    q_i     = (1/o_i) / Σ_j (1/o_j)                       (market vote share, o = odds used)
    s_i     = a · log q_i + b · log p̄_i
    p_mix_i = exp(s_i) / Σ_j exp(s_j)                     (softmax over the race's started field)
    EV_mix_i = p_mix_i · o_i

A race-level rescaling of p̄ cancels inside the softmax, so p̄ is not normalized first. The
coefficients for races of year Y are fitted (winner log-likelihood, race-day grouping irrelevant)
on the out-of-fold predictions of years 2010..Y−1 — the same yearly walk-forward as the boosters.

This module is the ONLY definition (the API computes the mixed value at read time from the stored
``win_prob`` / ``odds_used``; the research scripts evaluate with the same function). The mixed
value is a SEPARATE, parallel column: it never replaces ``expected_return``, the 1.2 highlight or
the S1–S5 conditions. The mixed probability is not a 1着率 and is never returned by the API.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .attention_rules import DISPLAYED_MARKET_EV_MODEL_VERSION

__all__ = [
    "MIX_COEFFICIENTS",
    "MIX_DISPLAY_ENABLED",
    "MIX_EV_THRESHOLD",
    "MIX_SOURCE_MODEL_VERSION",
    "MIX_VERSION",
    "MixCoefficients",
    "coefficients_for",
    "exceeds_mix_threshold",
    "mix_log_probabilities",
    "mix_probabilities",
]

MIX_VERSION = "mix-ens15-v1"

#: The mixture is fitted on THIS model's p̄ only (another model's p̄ needs its own fit).
MIX_SOURCE_MODEL_VERSION = DISPLAYED_MARKET_EV_MODEL_VERSION

#: Neutral marker threshold (ratio, STRICT ``>``). 1.0 and not 1.2: after mixing, EV_mix > 1.2 is
#: still over-stated (Σwon/Σp_mix 0.77 on 2,330 horses) while EV_mix > 1.0 is 0.95 (24,737).
MIX_EV_THRESHOLD = 1.0

#: Decided by the pre-registered held-out check (spec FR-006); see plan §4.
MIX_DISPLAY_ENABLED = True


@dataclass(frozen=True)
class MixCoefficients:
    """Frozen mixture coefficients for races of ``year`` (fit on years fit_from..fit_through)."""

    year: int
    a: float
    b: float
    fit_from_year: int
    fit_through_year: int
    n_races: int
    n_rows: int
    source_sha256: str


#: Frozen from scripts/roi_explore/market_mix_20261019/fit_mix.py (coefficients.json). The test
#: ``test_market_mix_coefficients_match_fit`` pins these to the fit output when it is present.
MIX_COEFFICIENTS: dict[int, MixCoefficients] = {
    2025: MixCoefficients(
        year=2025, a=0.2833450347492916, b=0.7278063033935696,
        fit_from_year=2010, fit_through_year=2024,
        n_races=49833, n_rows=705663,
        source_sha256="c4637b87dd83e079a09b38d025ea38b0b8ae42fcd6e7ca8a04614a3809e15ae4",
    ),
    2026: MixCoefficients(
        year=2026, a=0.2785412668821836, b=0.7320697007761336,
        fit_from_year=2010, fit_through_year=2025,
        n_races=53162, n_rows=751870,
        source_sha256="c4637b87dd83e079a09b38d025ea38b0b8ae42fcd6e7ca8a04614a3809e15ae4",
    ),
}


def coefficients_for(year: int) -> MixCoefficients | None:
    """The coefficients for races of ``year``, or None (the caller reports ``no_coefficients``)."""
    return MIX_COEFFICIENTS.get(int(year))


def _validated(win_probs: Sequence[float], odds: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(win_probs, dtype=np.float64)
    o = np.asarray(odds, dtype=np.float64)
    if p.ndim != 1 or o.ndim != 1 or p.shape != o.shape:
        raise ValueError("win_probs and odds must be 1-D sequences of the same length")
    if len(p) < 2:
        raise ValueError("a race needs at least two started horses")
    if not (np.all(np.isfinite(p)) and np.all(p > 0.0)):
        raise ValueError("every win_prob must be finite and > 0")
    if not (np.all(np.isfinite(o)) and np.all(o >= 1.0)):
        raise ValueError("every win odds must be finite and >= 1.0")
    return p, o


def mix_log_probabilities(
    win_probs: Sequence[float], odds: Sequence[float], a: float, b: float
) -> np.ndarray:
    """log p_mix for ONE race's full started field (log-sum-exp stabilized)."""
    p, o = _validated(win_probs, odds)
    inv = 1.0 / o
    log_q = np.log(inv) - np.log(inv.sum())
    s = float(a) * log_q + float(b) * np.log(p)
    m = s.max()
    return s - (m + np.log(np.exp(s - m).sum()))


def mix_probabilities(
    win_probs: Sequence[float], odds: Sequence[float], coef: MixCoefficients
) -> np.ndarray:
    """p_mix for ONE race's full started field (Σ = 1). Raises ValueError on invalid inputs."""
    return np.exp(mix_log_probabilities(win_probs, odds, coef.a, coef.b))


def exceeds_mix_threshold(expected_return_mix: float) -> bool:
    """True only when the mixed expected return is STRICTLY above ``MIX_EV_THRESHOLD``."""
    return expected_return_mix > MIX_EV_THRESHOLD
