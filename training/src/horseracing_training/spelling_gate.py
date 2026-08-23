"""Pure composition helpers for the Feature 098 spelling adoption gate."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

FORMULA = "primary_pooled AND guard_real_direction AND transportable"


def pool_diffs_by_day(
    parts: Sequence[Mapping[str, Sequence[float]]],
) -> dict[str, list[float]]:
    """Union disjoint scored windows without sharing their mutable value lists."""
    pooled: dict[str, list[float]] = {}
    for part in parts:
        for day, values in part.items():
            if day in pooled:
                raise AssertionError(
                    f"windows overlap on {day} — pooled bootstrap would double count"
                )
            pooled[day] = list(values)
    return pooled


def point_estimate(diffs_by_day: Mapping[str, Sequence[float]]) -> float:
    """Return the race-weighted mean paired difference used by the bootstrap."""
    values = [float(value) for day in diffs_by_day.values() for value in day]
    if not values:
        raise ValueError("cannot estimate a point from no paired differences")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("paired differences must all be finite")
    return sum(values) / len(values)


def leave_one_out_points(
    parts: Sequence[Mapping[str, Sequence[float]]],
) -> list[float]:
    """Return race-weighted pooled points after dropping each cutoff once."""
    if len(parts) < 2:
        raise ValueError("leave-one-cutoff-out requires at least two cutoffs")
    return [
        point_estimate(pool_diffs_by_day([part for j, part in enumerate(parts) if j != i]))
        for i in range(len(parts))
    ]


def _sign(value: float) -> int:
    if not math.isfinite(value):
        raise ValueError("transportability points must be finite")
    return (value > 0.0) - (value < 0.0)


def transportability(
    per_cutoff_points: Sequence[float],
    loo_points: Sequence[float],
    *,
    pooled_point: float,
    real_ci_low: float | None,
) -> dict[str, bool]:
    """Evaluate the three frozen FR-007a transportability conditions."""
    pooled_sign = _sign(float(pooled_point))
    per_cutoff_sign_ok = bool(per_cutoff_points) and all(
        _sign(float(point)) == pooled_sign for point in per_cutoff_points
    )
    loo_sign_ok = bool(loo_points) and all(
        _sign(float(point)) == pooled_sign for point in loo_points
    )
    real_not_contradicting = not (
        pooled_point < 0.0 and (real_ci_low is None or real_ci_low > 0.0)
    )
    ok = per_cutoff_sign_ok and loo_sign_ok and real_not_contradicting
    return {
        "per_cutoff_sign_ok": per_cutoff_sign_ok,
        "loo_sign_ok": loo_sign_ok,
        "real_not_contradicting": real_not_contradicting,
        "ok": ok,
    }


def verdict_precedence(
    *,
    runnable: bool,
    sufficient: bool,
    primary_pooled: bool,
    guard_real_direction: bool,
    transportable: bool,
) -> dict:
    """Apply the frozen FR-007 precedence and return a RegimeReport-style verdict."""
    facts = {
        "runnable": bool(runnable),
        "sufficient": bool(sufficient),
        "primary_pooled": bool(primary_pooled),
        "guard_real_direction": bool(guard_real_direction),
        "transportability": bool(transportable),
    }
    if not runnable or not sufficient:
        status = "NO_DECISION"
        cause = "not_runnable" if not runnable else "insufficient_eval_days"
    elif not (primary_pooled and guard_real_direction):
        status = "REJECT"
        cause = "primary_pooled AND guard_real_direction not both true"
    elif not transportable:
        status = "NO_DECISION"
        cause = "transportability_failed"
    else:
        status = "ADOPT"
        cause = FORMULA
    return {
        "status": status,
        "adopt": status == "ADOPT",
        "formula": FORMULA,
        "decision_reason": {"cause": cause, **facts},
    }
