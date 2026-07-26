"""Market-q stage-discount fitting for the top-3 chaos readout (Feature 084).

Feature 049 already owns the conditional-NLL objectives and deterministic
golden-section minimizer.  This module is deliberately a thin, market-specific
entry point over that implementation: it accepts q vectors and finishing
indices, but never reads the Feature 049 model-p artifact.

The dependency direction is ``probability -> eval``.  Keep this module free of
``horseracing_probability`` imports so the training orchestration can combine
this fit with the probability-side chaos distribution without creating a
cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

from .stage_discount import (
    DEFAULT_MIN_RACES,
    StageDiscount,
    TopkSample,
    _nll_stage2,
    _nll_stage3,
    fit_stage_discount,
)

# The sample type is intentionally identical to Feature 049's math contract.
# ``win`` contains market vote-share q here, not model p.
MarketLambdaSample = TopkSample


def conditional_nll_stage2(lam: float, samples: Sequence[MarketLambdaSample]) -> float:
    """Return Feature 049's stage-2 conditional NLL for market-q samples."""

    races = [
        (list(sample.win), sample.i1, sample.i2)
        for sample in samples
        if sample.i1 is not None and sample.i2 is not None
    ]
    return _nll_stage2(lam, races)


def conditional_nll_stage3(lam: float, samples: Sequence[MarketLambdaSample]) -> float:
    """Return Feature 049's stage-3 conditional NLL for market-q samples."""

    races = [
        (list(sample.win), sample.i1, sample.i2, sample.i3)
        for sample in samples
        if sample.i1 is not None and sample.i2 is not None and sample.i3 is not None
    ]
    return _nll_stage3(lam, races)


def fit_chaos_lambda(
    samples: Sequence[MarketLambdaSample],
    *,
    min_races: int = DEFAULT_MIN_RACES,
) -> StageDiscount:
    """Fit market-q λ2/λ3 with the exact Feature 049 mathematics.

    Results are labels only.  Insufficient samples and boundary-stuck optima
    retain Feature 049's auditable identity fallback.
    """

    return fit_stage_discount(list(samples), min_races=min_races)


# Descriptive alias for callers that prefer to make the market provenance
# explicit.  Both names execute the same single implementation.
fit_market_lambdas = fit_chaos_lambda


__all__ = [
    "MarketLambdaSample",
    "conditional_nll_stage2",
    "conditional_nll_stage3",
    "fit_chaos_lambda",
    "fit_market_lambdas",
]
