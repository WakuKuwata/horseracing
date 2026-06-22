"""Probability-consistency validation, fail-fast (constitution IV, research R5).

Per horse: 0 <= win <= top2 <= top3 <= 1 (strict, float epsilon).
Race sums: |Σ_label - target| <= tolerance[label], where target = min(k, N) so
small fields (N < k) are handled correctly (every horse trivially in top-k).
"""

from __future__ import annotations

from .predictor import Prediction

_EPS = 1e-9

DEFAULT_TOLERANCE = {"win": 0.05, "top2": 0.10, "top3": 0.15}


class ConsistencyError(ValueError):
    """Raised when predictions violate range/monotonicity or race-sum tolerance."""


def check_consistency(
    predictions: dict[str, Prediction], tolerance: dict[str, float] | None = None
) -> None:
    tol = tolerance or DEFAULT_TOLERANCE
    n = len(predictions)
    if n == 0:
        raise ConsistencyError("empty prediction set")

    sums = {"win": 0.0, "top2": 0.0, "top3": 0.0}
    for horse_id, p in predictions.items():
        if (
            p.win < -_EPS
            or p.win > p.top2 + _EPS
            or p.top2 > p.top3 + _EPS
            or p.top3 > 1 + _EPS
        ):
            raise ConsistencyError(
                f"range/monotonicity violated for {horse_id}: "
                f"win={p.win}, top2={p.top2}, top3={p.top3}"
            )
        sums["win"] += p.win
        sums["top2"] += p.top2
        sums["top3"] += p.top3

    for label, k in (("win", 1), ("top2", 2), ("top3", 3)):
        target = min(k, n)
        if abs(sums[label] - target) > tol[label]:
            raise ConsistencyError(
                f"race-sum for {label} = {sums[label]:.4f}, expected ~{target} "
                f"(tolerance {tol[label]})"
            )
