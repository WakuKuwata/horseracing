"""Diagnose where win-pool-derived joint probabilities stop matching race outcomes.

The probability engine turns one closing win-share vector into every ordered and unordered
combination market.  Marginal win calibration therefore does not establish that its sequential
ranking law is right.  This module asks the narrower question: how much predictive fit and
reliability are added or lost by that frozen derivation, especially in the odds-selected tail
that motivated the diagnostic.

The result is deliberately an instrument, not an adoption gate.  Its residuals still mix errors
in the closing-odds input, information unique to combination pools, Plackett--Luce assumptions,
and timing differences.  Pre-registration: ``docs/plan/prereg-joint-calibration.md`` (rev2).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .bootstrap import (
    race_day_cluster_bootstrap_ci_v1,
    race_day_cluster_ratio_bootstrap_ci_v1,
)
from .stage_discount import StageDiscount

#: The 009 engine, injected by training. eval must NOT import horseracing_probability: the
#: dependency arrow runs probability -> eval, so a reverse import only fails at call time.
JointFn = Callable[..., Any]

CONTRACT_VERSION = "joint-calibration-v1"

# Wide has three winning pairs, so it has no unique realized outcome for categorical NLL.
BET_TYPES_NLL: tuple[str, ...] = ("exacta", "quinella", "trio", "trifecta")
WIDE_MIN_FIELD = 8
BIN_EDGES: tuple[float, ...] = (
    1e-6,
    1e-5,
    1e-4,
    1e-3,
    3e-3,
    1e-2,
    3e-2,
    1e-1,
    3e-1,
    1.0,
)
FIELD_BUCKETS: tuple[str, ...] = ("<=7", "8-11", "12-15", "16+")
MARKET_LAMBDA2, MARKET_LAMBDA3 = 0.75, 0.70
ARMS: tuple[str, ...] = ("identity", "market_current", "indep_normalized", "uniform")
SELECT_THRESHOLDS: tuple[float, ...] = (1.0, 1.5)
BOOTSTRAP_B, BOOTSTRAP_SEED = 2000, 20260731

_BET_TYPES_RELIABILITY: tuple[str, ...] = (*BET_TYPES_NLL, "wide")
_SELECT_BET_TYPES: tuple[str, ...] = ("quinella", "wide", "trio")
_NAN = float("nan")


class JointCalibrationError(RuntimeError):
    """The diagnostic cannot preserve its frozen estimand for the supplied input."""


@dataclass(frozen=True)
class JointCalibRace:
    """One eligible race, aligned to the final started field and its unique top three."""

    race_id: str
    day: str
    numbers: tuple[int, ...]
    q: tuple[float, ...]
    top3: tuple[int, int, int]
    grid: dict[str, dict[tuple[int, ...], float]] | None = None


@dataclass(frozen=True)
class BootstrapEstimate:
    """A point statistic and its race-day cluster percentile interval."""

    point: float
    ci_low: float | None
    ci_high: float | None
    b: int
    seed: int
    n_days: int
    no_decision: bool

    def to_dict(self) -> dict[str, Any]:
        return vars(self) | {}


@dataclass(frozen=True)
class NLLResult:
    """One-race-one-observation NLL excess over that race's uniform outcome space."""

    arm: str
    bet_type: str
    field_bucket: str
    n_races: int
    excess_over_uniform: BootstrapEstimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "bet_type": self.bet_type,
            "field_bucket": self.field_bucket,
            "n_races": self.n_races,
            "excess_over_uniform": self.excess_over_uniform.to_dict(),
        }


@dataclass(frozen=True)
class ReliabilityBinResult:
    """Cell-weighted reliability plus the equally weighted per-race calibration gap."""

    arm: str
    bet_type: str
    field_bucket: str
    lower: float
    upper: float
    upper_inclusive: bool
    n_races: int
    n_cells: int
    n_positive: int
    predicted_mean: BootstrapEstimate
    realized_rate: BootstrapEstimate
    micro_gap: BootstrapEstimate
    race_normalized_gap: BootstrapEstimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "bet_type": self.bet_type,
            "field_bucket": self.field_bucket,
            "lower": self.lower,
            "upper": self.upper,
            "upper_inclusive": self.upper_inclusive,
            "n_races": self.n_races,
            "n_cells": self.n_cells,
            "n_positive": self.n_positive,
            "predicted_mean_point": self.predicted_mean.point,
            "realized_rate_point": self.realized_rate.point,
            "micro_gap_point": self.micro_gap.point,
            "race_normalized_gap_point": self.race_normalized_gap.point,
            "predicted_mean": self.predicted_mean.to_dict(),
            "realized_rate": self.realized_rate.to_dict(),
            "micro_gap": self.micro_gap.to_dict(),
            "race_normalized_gap": self.race_normalized_gap.to_dict(),
        }


@dataclass(frozen=True)
class SelectedSubsetResult:
    """Calibration on a result-blind mask frozen from current-engine probability and real odds."""

    arm: str
    bet_type: str
    threshold: float
    n_grid_races: int
    n_selected: int
    n_positive: int
    predicted_sum: float
    realized_sum: float
    predicted_to_realized: BootstrapEstimate
    predicted_minus_realized: BootstrapEstimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "bet_type": self.bet_type,
            "threshold": self.threshold,
            "n_grid_races": self.n_grid_races,
            "n_selected": self.n_selected,
            "n_positive": self.n_positive,
            "predicted_sum": self.predicted_sum,
            "realized_sum": self.realized_sum,
            "predicted_to_realized": self.predicted_to_realized.to_dict(),
            "predicted_minus_realized": self.predicted_minus_realized.to_dict(),
        }


@dataclass(frozen=True)
class WideInclusionResult:
    """The N>=8 three-positive-cell inclusion-mass check for one arm."""

    arm: str
    n_races: int
    n_cells: int
    n_positive: int
    predicted_sum: float
    realized_sum: float
    predicted_to_realized: BootstrapEstimate
    predicted_minus_realized: BootstrapEstimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n_races": self.n_races,
            "n_cells": self.n_cells,
            "n_positive": self.n_positive,
            "predicted_sum": self.predicted_sum,
            "realized_sum": self.realized_sum,
            "predicted_to_realized": self.predicted_to_realized.to_dict(),
            "predicted_minus_realized": self.predicted_minus_realized.to_dict(),
        }


@dataclass(frozen=True)
class _RatioRun:
    estimate: BootstrapEstimate
    replicates: tuple[float, ...]


def _validate_race(race: JointCalibRace) -> None:
    prefix = f"race {race.race_id}: "
    if len(race.numbers) != len(race.q):
        raise JointCalibrationError(prefix + "numbers and q have different lengths")
    if tuple(race.numbers) != tuple(sorted(race.numbers)) or len(set(race.numbers)) != len(
        race.numbers
    ):
        raise JointCalibrationError(prefix + "numbers must be unique and ascending")
    if not race.q or any(not math.isfinite(p) or p <= 0.0 for p in race.q):
        raise JointCalibrationError(prefix + "q must contain only finite positive values")
    if abs(math.fsum(race.q) - 1.0) >= 1e-9:
        raise JointCalibrationError(prefix + "q must sum to one within the frozen tolerance")
    if len(race.top3) != 3 or len(set(race.top3)) != 3:
        raise JointCalibrationError(prefix + "top3 must contain three distinct horse numbers")
    if any(number not in set(race.numbers) for number in race.top3):
        raise JointCalibrationError(prefix + "top3 contains a horse outside the started field")


def _validated_vectors(
    q: Sequence[float], numbers: Sequence[int]
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    qs = tuple(float(p) for p in q)
    nums = tuple(int(number) for number in numbers)
    if len(qs) != len(nums) or not qs:
        raise JointCalibrationError("q and numbers must be non-empty and aligned")
    if nums != tuple(sorted(nums)) or len(set(nums)) != len(nums):
        raise JointCalibrationError("numbers must be unique and ascending")
    if any(not math.isfinite(p) or p <= 0.0 for p in qs):
        raise JointCalibrationError("q must contain only finite positive values")
    return qs, nums


def stage_losses(
    race: JointCalibRace, *, lambda2: float, lambda3: float
) -> tuple[float, float, float]:
    """Return the frozen stage-1, stage-2, and stage-3 conditional log losses."""
    _validate_race(race)
    if not math.isfinite(lambda2) or not math.isfinite(lambda3):
        raise JointCalibrationError("stage lambdas must be finite")
    q_by_number = dict(zip(race.numbers, race.q, strict=True))
    first, second, third = race.top3
    weights2 = {number: p**lambda2 for number, p in q_by_number.items()}
    weights3 = {number: p**lambda3 for number, p in q_by_number.items()}
    denominator2 = math.fsum(weight for number, weight in weights2.items() if number != first)
    denominator3 = math.fsum(
        weight for number, weight in weights3.items() if number not in {first, second}
    )
    probability2 = weights2[second] / denominator2 if denominator2 > 0.0 else 0.0
    probability3 = weights3[third] / denominator3 if denominator3 > 0.0 else 0.0
    if probability2 <= 0.0 or probability3 <= 0.0:
        raise JointCalibrationError(f"race {race.race_id}: degenerate stage denominator")
    return (-math.log(q_by_number[first]), -math.log(probability2), -math.log(probability3))


def normalized_independent(
    q: Sequence[float], numbers: Sequence[int], k: int, *, ordered: bool
) -> dict[tuple[int, ...], float]:
    """Return the normalized distinct-horse independent-product baseline for k=2 or k=3."""
    qs, nums = _validated_vectors(q, numbers)
    if k not in (2, 3):
        raise JointCalibrationError("normalized_independent supports only k=2 or k=3")
    indexed = tuple(zip(nums, qs, strict=True))
    denominator = math.fsum(
        math.prod(p for _, p in selection) for selection in itertools.permutations(indexed, k)
    )
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise JointCalibrationError("independent-product normalizer is not finite and positive")
    selections = (
        itertools.permutations(indexed, k)
        if ordered
        else itertools.combinations(indexed, k)
    )
    multiplier = 1 if ordered else math.factorial(k)
    return {
        tuple(number for number, _ in selection):
        multiplier * math.prod(p for _, p in selection) / denominator
        for selection in selections
    }


def _wide_from_trio(
    trio: dict[tuple[int, ...], float], numbers: Sequence[int]
) -> dict[tuple[int, ...], float]:
    wide = {pair: 0.0 for pair in itertools.combinations(numbers, 2)}
    for triple, probability in trio.items():
        for pair in itertools.combinations(triple, 2):
            wide[pair] += probability
    return wide


def _uniform_distribution(
    numbers: tuple[int, ...], k: int, *, ordered: bool
) -> dict[tuple[int, ...], float]:
    selections = tuple(
        itertools.permutations(numbers, k) if ordered else itertools.combinations(numbers, k)
    )
    probability = 1.0 / len(selections)
    return {selection: probability for selection in selections}


def _canonical_unordered(key: frozenset[str]) -> tuple[int, ...]:
    # Iterating a frozenset caused a past key-order bug here.  Sorting after conversion is part of
    # the serialized contract, not just presentation cleanup.
    return tuple(sorted(int(value) for value in key))


def bet_type_distributions(
    race: JointCalibRace, *, arm: str, joint_fn: JointFn | None = None,
) -> dict[str, dict[tuple[int, ...], float]]:
    """Derive canonical-tuple distributions for one frozen diagnostic arm.

    ``joint_fn`` is the 009 engine, INJECTED rather than imported. The dependency arrow in this
    repo runs probability -> eval (probability declares horseracing-eval; eval does not declare
    horseracing-probability), so importing the engine here — even lazily — is a reverse dependency
    that only fails at call time. ``chaos_lambda`` avoids the same import for the same reason.
    Training owns both workspaces and passes ``joint_probabilities`` in.
    """
    _validate_race(race)
    if arm not in ARMS:
        raise JointCalibrationError(f"unknown joint-calibration arm: {arm}")
    numbers = tuple(race.numbers)
    if arm in {"identity", "market_current"}:
        if joint_fn is None:
            raise JointCalibrationError(
                f"arm {arm!r} needs the 009 engine; pass joint_fn=joint_probabilities "
                "(eval must not import horseracing_probability)"
            )
        discount = (
            None
            if arm == "identity"
            else StageDiscount(lambda2=MARKET_LAMBDA2, lambda3=MARKET_LAMBDA3)
        )
        joint = joint_fn(
            {str(number): p for number, p in zip(numbers, race.q, strict=True)},
            field_size=len(numbers),
            stage_discount=discount,
        )
        result = {
            "exacta": {
                tuple(int(value) for value in key): probability
                for key, probability in joint.exacta.items()
            },
            "quinella": {
                _canonical_unordered(key): probability
                for key, probability in joint.quinella.items()
            },
            "trio": {
                _canonical_unordered(key): probability for key, probability in joint.trio.items()
            },
            "trifecta": {
                tuple(int(value) for value in key): probability
                for key, probability in joint.trifecta.items()
            },
        }
        if len(numbers) >= WIDE_MIN_FIELD and joint.wide is not None:
            result["wide"] = {
                _canonical_unordered(key): probability for key, probability in joint.wide.items()
            }
        return result

    if arm == "indep_normalized":
        trio = normalized_independent(race.q, numbers, 3, ordered=False)
        result = {
            "exacta": normalized_independent(race.q, numbers, 2, ordered=True),
            "quinella": normalized_independent(race.q, numbers, 2, ordered=False),
            "trio": trio,
            "trifecta": normalized_independent(race.q, numbers, 3, ordered=True),
        }
        if len(numbers) >= WIDE_MIN_FIELD:
            result["wide"] = _wide_from_trio(trio, numbers)
        return result

    result = {
        "exacta": _uniform_distribution(numbers, 2, ordered=True),
        "quinella": _uniform_distribution(numbers, 2, ordered=False),
        "trio": _uniform_distribution(numbers, 3, ordered=False),
        "trifecta": _uniform_distribution(numbers, 3, ordered=True),
    }
    if len(numbers) >= WIDE_MIN_FIELD:
        wide_probability = 3.0 / math.comb(len(numbers), 2)
        result["wide"] = {
            pair: wide_probability for pair in itertools.combinations(numbers, 2)
        }
    return result


def realized_keys(
    bet_type: str, top3: Sequence[int]
) -> tuple[tuple[int, ...], ...]:
    """Return the canonical positive cell or cells for a unique top-three result."""
    finish = tuple(int(number) for number in top3)
    if len(finish) != 3 or len(set(finish)) != 3:
        raise JointCalibrationError("top3 must contain three distinct horse numbers")
    if bet_type == "exacta":
        return (finish[:2],)
    if bet_type == "quinella":
        return (tuple(sorted(finish[:2])),)
    if bet_type == "trifecta":
        return (finish,)
    if bet_type == "trio":
        return (tuple(sorted(finish)),)
    if bet_type == "wide":
        return tuple(sorted(tuple(sorted(pair)) for pair in itertools.combinations(finish, 2)))
    raise JointCalibrationError(f"unknown bet type: {bet_type}")


def _field_bucket(field_size: int) -> str:
    if field_size <= 7:
        return "<=7"
    if field_size <= 11:
        return "8-11"
    if field_size <= 15:
        return "12-15"
    return "16+"


def _bin_bounds(index: int) -> tuple[float, float, bool]:
    lower = 0.0 if index == 0 else BIN_EDGES[index - 1]
    return lower, BIN_EDGES[index], index == len(BIN_EDGES) - 1


def _bin_index(probability: float) -> int:
    return min(bisect_right(BIN_EDGES, probability), len(BIN_EDGES) - 1)


def _nan_estimate(*, b: int, seed: int, n_days: int) -> BootstrapEstimate:
    return BootstrapEstimate(_NAN, _NAN, _NAN, b, seed, n_days, True)


def _estimate_from_values(
    values_by_day: dict[str, list[float]], *, b: int, seed: int
) -> BootstrapEstimate:
    if not any(values_by_day.values()):
        return _nan_estimate(b=b, seed=seed, n_days=len(values_by_day))
    ci = race_day_cluster_bootstrap_ci_v1(values_by_day, b=b, seed=seed)
    ci_low = ci.point if ci.no_decision and ci.n_days == 1 else ci.ci_low
    ci_high = ci.point if ci.no_decision and ci.n_days == 1 else ci.ci_high
    return BootstrapEstimate(
        ci.point, ci_low, ci_high, ci.b, ci.seed, ci.n_days, ci.no_decision
    )


def _ratio_run(
    numerator_by_day: dict[str, list[float]],
    denominator_by_day: dict[str, list[float]],
    *,
    b: int,
    seed: int,
) -> _RatioRun:
    total_denominator = math.fsum(math.fsum(values) for values in denominator_by_day.values())
    if total_denominator <= 0.0:
        return _RatioRun(
            _nan_estimate(b=b, seed=seed, n_days=len(denominator_by_day)), ()
        )
    ci = race_day_cluster_ratio_bootstrap_ci_v1(
        numerator_by_day, denominator_by_day, b=b, seed=seed
    )
    ci_low = ci.point if ci.no_decision and ci.n_days == 1 else ci.ci_low
    ci_high = ci.point if ci.no_decision and ci.n_days == 1 else ci.ci_high
    estimate = BootstrapEstimate(
        ci.point, ci_low, ci_high, ci.b, ci.seed, ci.n_days, ci.no_decision
    )
    return _RatioRun(estimate, ci.replicates)


def _with_replicate_mask(run: _RatioRun, valid: Sequence[bool]) -> BootstrapEstimate:
    if math.isnan(run.estimate.point):
        return run.estimate
    if run.estimate.no_decision:
        return run.estimate
    values = np.asarray(run.replicates, dtype=float)
    mask = np.asarray(valid, dtype=bool) & np.isfinite(values)
    if not mask.any():
        return BootstrapEstimate(
            run.estimate.point,
            _NAN,
            _NAN,
            run.estimate.b,
            run.estimate.seed,
            run.estimate.n_days,
            True,
        )
    return BootstrapEstimate(
        run.estimate.point,
        float(np.percentile(values[mask], 2.5)),
        float(np.percentile(values[mask], 97.5)),
        run.estimate.b,
        run.estimate.seed,
        run.estimate.n_days,
        False,
    )


def _day_lists(
    races: Sequence[JointCalibRace], values: Sequence[float]
) -> dict[str, list[float]]:
    by_day = {day: [] for day in sorted({race.day for race in races})}
    for race, value in zip(races, values, strict=True):
        by_day[race.day].append(float(value))
    return by_day


def _mean_with_denominator(
    races: Sequence[JointCalibRace],
    values: Sequence[float],
    included: Sequence[bool],
    *,
    b: int,
    seed: int,
) -> BootstrapEstimate:
    numerator = _day_lists(
        races, [value if keep else 0.0 for value, keep in zip(values, included, strict=True)]
    )
    denominator = _day_lists(races, [float(keep) for keep in included])
    return _ratio_run(numerator, denominator, b=b, seed=seed).estimate


def _sum_run(
    races: Sequence[JointCalibRace], values: Sequence[float], *, b: int, seed: int
) -> _RatioRun:
    days = sorted({race.day for race in races})
    by_day = {day: [0.0] for day in days}
    for race, value in zip(races, values, strict=True):
        by_day[race.day][0] += float(value)
    # Each resample contains exactly n_days day blocks, so these denominator sums are always one.
    unit = 1.0 / len(days)
    denominator = {day: [unit] for day in days}
    return _ratio_run(by_day, denominator, b=b, seed=seed)


def _grid_hash(races: Sequence[JointCalibRace]) -> tuple[list[str], str | None]:
    rows: list[list[Any]] = []
    race_ids: list[str] = []
    for race in sorted(races, key=lambda item: (item.race_id, item.day)):
        if race.grid is None:
            continue
        race_ids.append(race.race_id)
        for bet_type in sorted(race.grid):
            for key, odds in sorted(race.grid[bet_type].items()):
                # 999.9 is the WIN-odds sentinel and must NOT be applied here: exotic prices are
                # a different scale entirely (observed max 99,999.9), so a combination quoted at
                # exactly 999.9 is an ordinary long shot — 27 of them exist in the real grids.
                # Rejecting it aborted the whole pre-registered run on one trio price.
                if not isinstance(odds, (int, float)) or not math.isfinite(odds) or odds <= 0.0:
                    raise JointCalibrationError(
                        f"race {race.race_id}: grid odds must be finite and positive, got {odds!r}"
                    )
                rows.append([race.race_id, race.day, bet_type, list(key), float(odds)])
    if not rows:
        return race_ids, None
    encoded = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode()
    return race_ids, hashlib.sha256(encoded).hexdigest()


def _stage_loss_block(
    races: Sequence[JointCalibRace], arms: Sequence[str], *, b: int, seed: int
) -> dict[str, Any]:
    lambda_arms = {
        "identity": (1.0, 1.0),
        "market_current": (MARKET_LAMBDA2, MARKET_LAMBDA3),
    }
    by_arm: dict[str, Any] = {}
    raw: dict[str, list[tuple[float, float, float]]] = {}
    for arm in arms:
        if arm not in lambda_arms:
            continue
        lambda2, lambda3 = lambda_arms[arm]
        losses = [stage_losses(race, lambda2=lambda2, lambda3=lambda3) for race in races]
        raw[arm] = losses
        labels = {
            "L1": [loss[0] for loss in losses],
            "L2": [loss[1] for loss in losses],
            "L3": [loss[2] for loss in losses],
            "L2_plus_L3": [loss[1] + loss[2] for loss in losses],
        }
        by_arm[arm] = {
            label: _estimate_from_values(_day_lists(races, values), b=b, seed=seed).to_dict()
            for label, values in labels.items()
        }
    contrast: dict[str, Any] | None = None
    if "identity" in raw and "market_current" in raw:
        differences = [
            (current[1] + current[2]) - (identity[1] + identity[2])
            for identity, current in zip(raw["identity"], raw["market_current"], strict=True)
        ]
        contrast = _estimate_from_values(_day_lists(races, differences), b=b, seed=seed).to_dict()
    return {
        "grain": "one race per stage loss",
        "negative_control": "L1; identical across lambda arms",
        "by_arm": by_arm,
        "undefined_arms": [arm for arm in arms if arm not in lambda_arms],
        "primary_contrast": {
            "definition": "market_current(L2+L3) - identity(L2+L3)",
            "estimate": contrast,
            "inferential_claim": "the only pre-registered inferential contrast",
        },
    }


def _nll_block(
    races: Sequence[JointCalibRace],
    arms: Sequence[str],
    distributions: dict[str, list[dict[str, dict[tuple[int, ...], float]]]],
    *,
    b: int,
    seed: int,
) -> dict[str, Any]:
    results: list[NLLResult] = []
    for arm in arms:
        for bet_type in BET_TYPES_NLL:
            values: list[float] = []
            for race, race_distributions in zip(races, distributions[arm], strict=True):
                distribution = race_distributions[bet_type]
                key = realized_keys(bet_type, race.top3)[0]
                probability = distribution.get(key, 0.0)
                if probability <= 0.0 or not math.isfinite(probability):
                    raise JointCalibrationError(
                        f"race {race.race_id}: {arm}/{bet_type} realized probability is invalid"
                    )
                values.append(-math.log(probability) - math.log(len(distribution)))
            for bucket in ("all", *FIELD_BUCKETS):
                included = [
                    bucket == "all" or _field_bucket(len(race.numbers)) == bucket
                    for race in races
                ]
                estimate = _mean_with_denominator(
                    races, values, included, b=b, seed=seed
                )
                results.append(
                    NLLResult(
                        arm,
                        bet_type,
                        bucket,
                        sum(included),
                        estimate,
                    )
                )
    return {
        "estimand": "per-race NLL - log(K_r); raw NLL is intentionally not reported",
        "grain": "one realized categorical outcome per race",
        "cells": [result.to_dict() for result in results],
    }


def _reliability_stats(
    race: JointCalibRace,
    distribution: dict[tuple[int, ...], float],
    bet_type: str,
) -> tuple[list[int], list[float], list[int]]:
    counts = [0] * len(BIN_EDGES)
    probability_sums = [0.0] * len(BIN_EDGES)
    positive_sums = [0] * len(BIN_EDGES)
    positives = set(realized_keys(bet_type, race.top3))
    for key, probability in distribution.items():
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise JointCalibrationError(
                f"race {race.race_id}: {bet_type} contains an invalid cell probability"
            )
        index = _bin_index(probability)
        counts[index] += 1
        probability_sums[index] += probability
        positive_sums[index] += int(key in positives)
    return counts, probability_sums, positive_sums


def _reliability_block(
    races: Sequence[JointCalibRace],
    arms: Sequence[str],
    distributions: dict[str, list[dict[str, dict[tuple[int, ...], float]]]],
    *,
    b: int,
    seed: int,
) -> dict[str, Any]:
    rows: list[ReliabilityBinResult] = []
    for arm in arms:
        for bet_type in _BET_TYPES_RELIABILITY:
            stats = [
                _reliability_stats(race, race_distribution[bet_type], bet_type)
                if bet_type in race_distribution
                else ([0] * len(BIN_EDGES), [0.0] * len(BIN_EDGES), [0] * len(BIN_EDGES))
                for race, race_distribution in zip(races, distributions[arm], strict=True)
            ]
            for bucket in ("all", *FIELD_BUCKETS):
                bucket_mask = [
                    bucket == "all" or _field_bucket(len(race.numbers)) == bucket
                    for race in races
                ]
                for index in range(len(BIN_EDGES)):
                    counts = [
                        stat[0][index] if keep else 0
                        for stat, keep in zip(stats, bucket_mask, strict=True)
                    ]
                    predicted = [
                        stat[1][index] if keep else 0.0
                        for stat, keep in zip(stats, bucket_mask, strict=True)
                    ]
                    positive = [
                        stat[2][index] if keep else 0
                        for stat, keep in zip(stats, bucket_mask, strict=True)
                    ]
                    predicted_run = _ratio_run(
                        _day_lists(races, predicted),
                        _day_lists(races, counts),
                        b=b,
                        seed=seed,
                    )
                    realized_run = _ratio_run(
                        _day_lists(races, positive),
                        _day_lists(races, counts),
                        b=b,
                        seed=seed,
                    )
                    micro_gap_run = _ratio_run(
                        _day_lists(
                            races,
                            [p - y for p, y in zip(predicted, positive, strict=True)],
                        ),
                        _day_lists(races, counts),
                        b=b,
                        seed=seed,
                    )
                    race_gaps = [
                        (p - y) / count if count else 0.0
                        for p, y, count in zip(predicted, positive, counts, strict=True)
                    ]
                    present = [count > 0 for count in counts]
                    race_gap_run = _ratio_run(
                        _day_lists(races, race_gaps),
                        _day_lists(races, [float(value) for value in present]),
                        b=b,
                        seed=seed,
                    )
                    n_positive = sum(positive)
                    if n_positive == 0:
                        realized_estimate = _nan_estimate(
                            b=b, seed=seed, n_days=len({race.day for race in races})
                        )
                        micro_gap_estimate = realized_estimate
                        race_gap_estimate = realized_estimate
                    else:
                        valid_replicates = [
                            math.isfinite(value) and value > 0.0
                            for value in realized_run.replicates
                        ]
                        realized_estimate = _with_replicate_mask(
                            realized_run, valid_replicates
                        )
                        micro_gap_estimate = _with_replicate_mask(
                            micro_gap_run, valid_replicates
                        )
                        race_gap_estimate = _with_replicate_mask(
                            race_gap_run, valid_replicates
                        )
                    lower, upper, upper_inclusive = _bin_bounds(index)
                    rows.append(
                        ReliabilityBinResult(
                            arm=arm,
                            bet_type=bet_type,
                            field_bucket=bucket,
                            lower=lower,
                            upper=upper,
                            upper_inclusive=upper_inclusive,
                            n_races=sum(present),
                            n_cells=sum(counts),
                            n_positive=n_positive,
                            predicted_mean=predicted_run.estimate,
                            realized_rate=realized_estimate,
                            micro_gap=micro_gap_estimate,
                            race_normalized_gap=race_gap_estimate,
                        )
                    )
    return {
        "estimand": "cell-weighted micro reliability; distinct from one-race-one-sample NLL",
        "race_normalized_note": "each race represented in a bin receives equal weight",
        "interval": "paired race-day cluster percentile bootstrap; never Wilson",
        "bins": [row.to_dict() for row in rows],
    }


def _canonical_grid_key(bet_type: str, key: tuple[int, ...]) -> tuple[int, ...]:
    values = tuple(int(value) for value in key)
    if bet_type in {"quinella", "wide", "trio"}:
        canonical = tuple(sorted(values))
        if values != canonical:
            raise JointCalibrationError(
                f"{bet_type} grid key {values} is not the required ascending tuple"
            )
        return canonical
    return values


def _selected_subset_block(
    races: Sequence[JointCalibRace],
    arms: Sequence[str],
    distributions: dict[str, list[dict[str, dict[tuple[int, ...], float]]]],
    selector_distributions: Sequence[dict[str, dict[tuple[int, ...], float]]],
    *,
    b: int,
    seed: int,
) -> dict[str, Any]:
    rows: list[SelectedSubsetResult] = []
    for bet_type in _SELECT_BET_TYPES:
        for threshold in SELECT_THRESHOLDS:
            masks: list[tuple[tuple[int, ...], ...]] = []
            grid_races: list[bool] = []
            for race, selector in zip(races, selector_distributions, strict=True):
                grid = None if race.grid is None else race.grid.get(bet_type)
                if grid is None or bet_type not in selector:
                    masks.append(())
                    grid_races.append(False)
                    continue
                selected: list[tuple[int, ...]] = []
                for raw_key, odds in sorted(grid.items()):
                    key = _canonical_grid_key(bet_type, raw_key)
                    probability = selector[bet_type].get(key)
                    if probability is None:
                        continue
                    # Same rule as _grid_hash: 999.9 is the WIN sentinel, never an exotic one.
                    # Excluding it here would additionally bias the selected subset by silently
                    # dropping long shots — exactly the cells this endpoint exists to measure.
                    if not isinstance(odds, (int, float)) or not math.isfinite(odds) or odds <= 0.0:
                        raise JointCalibrationError(
                            f"race {race.race_id}: grid odds must be finite and positive, "
                            f"got {odds!r}"
                        )
                    if probability * odds >= threshold:
                        selected.append(key)
                masks.append(tuple(selected))
                grid_races.append(True)

            realized = [
                sum(key in set(realized_keys(bet_type, race.top3)) for key in mask)
                for race, mask in zip(races, masks, strict=True)
            ]
            for arm in arms:
                predicted = [
                    math.fsum(distribution.get(key, 0.0) for key in mask)
                    for distribution, mask in zip(
                        (item.get(bet_type, {}) for item in distributions[arm]),
                        masks,
                        strict=True,
                    )
                ]
                ratio_run = _ratio_run(
                    _day_lists(races, predicted),
                    _day_lists(races, realized),
                    b=b,
                    seed=seed,
                )
                gap_run = _sum_run(
                    races,
                    [p - y for p, y in zip(predicted, realized, strict=True)],
                    b=b,
                    seed=seed,
                )
                n_positive = sum(realized)
                if n_positive == 0:
                    ratio_estimate = _nan_estimate(
                        b=b, seed=seed, n_days=len({race.day for race in races})
                    )
                    gap_estimate = ratio_estimate
                else:
                    valid_replicates = [math.isfinite(value) for value in ratio_run.replicates]
                    ratio_estimate = _with_replicate_mask(ratio_run, valid_replicates)
                    gap_estimate = _with_replicate_mask(gap_run, valid_replicates)
                rows.append(
                    SelectedSubsetResult(
                        arm=arm,
                        bet_type=bet_type,
                        threshold=threshold,
                        n_grid_races=sum(grid_races),
                        n_selected=sum(len(mask) for mask in masks),
                        n_positive=n_positive,
                        predicted_sum=math.fsum(predicted),
                        realized_sum=float(n_positive),
                        predicted_to_realized=ratio_estimate,
                        predicted_minus_realized=gap_estimate,
                    )
                )
    return {
        "selector": "market_current probability * real low-quote odds >= threshold",
        "mask_contract": "one result-blind market_current mask reused by every arm",
        "thresholds": list(SELECT_THRESHOLDS),
        "cells": [row.to_dict() for row in rows],
    }


def _wide_inclusion_block(
    races: Sequence[JointCalibRace],
    arms: Sequence[str],
    distributions: dict[str, list[dict[str, dict[tuple[int, ...], float]]]],
    *,
    b: int,
    seed: int,
) -> dict[str, Any]:
    rows: list[WideInclusionResult] = []
    eligible = [len(race.numbers) >= WIDE_MIN_FIELD for race in races]
    realized = [3.0 if keep else 0.0 for keep in eligible]
    for arm in arms:
        predicted = [
            math.fsum(distribution.get("wide", {}).values()) if keep else 0.0
            for distribution, keep in zip(distributions[arm], eligible, strict=True)
        ]
        ratio_run = _ratio_run(
            _day_lists(races, predicted), _day_lists(races, realized), b=b, seed=seed
        )
        gap_run = _sum_run(
            races,
            [p - y for p, y in zip(predicted, realized, strict=True)],
            b=b,
            seed=seed,
        )
        n_races = sum(eligible)
        n_cells = sum(
            len(distribution.get("wide", {}))
            for distribution, keep in zip(distributions[arm], eligible, strict=True)
            if keep
        )
        rows.append(
            WideInclusionResult(
                arm=arm,
                n_races=n_races,
                n_cells=n_cells,
                n_positive=3 * n_races,
                predicted_sum=math.fsum(predicted),
                realized_sum=math.fsum(realized),
                predicted_to_realized=ratio_run.estimate,
                predicted_minus_realized=gap_run.estimate,
            )
        )
    return {
        "minimum_field_size": WIDE_MIN_FIELD,
        "mass_contract_per_race": 3.0,
        "categorical_nll": False,
        "by_arm": [row.to_dict() for row in rows],
    }


def evaluate(
    races: Sequence[JointCalibRace],
    *,
    arms: Sequence[str] = ARMS,
    b: int = BOOTSTRAP_B,
    seed: int = BOOTSTRAP_SEED,
    joint_fn: JointFn | None = None,
) -> dict[str, Any]:
    """Evaluate every pre-registered arm and return the frozen JSON-oriented payload."""
    races = tuple(races)
    arms = tuple(arms)
    if not races:
        raise JointCalibrationError("no races")
    if not arms or len(set(arms)) != len(arms) or any(arm not in ARMS for arm in arms):
        raise JointCalibrationError("arms must be a non-empty unique subset of ARMS")
    if b <= 0:
        raise JointCalibrationError("bootstrap replicate count must be positive")
    for race in races:
        _validate_race(race)

    distributions = {
        arm: [bet_type_distributions(race, arm=arm, joint_fn=joint_fn) for race in races]
        for arm in arms
    }
    # Selection is frozen to current engine probabilities even when callers request a subset of
    # reporting arms.  Otherwise changing ``arms`` would silently change the selected population.
    selector_distributions = (
        distributions["market_current"]
        if "market_current" in distributions
        else [bet_type_distributions(race, arm="market_current", joint_fn=joint_fn)
               for race in races]
    )
    grid_race_ids, grid_hash = _grid_hash(races)
    days = sorted({race.day for race in races})
    return {
        "instrument_contract": {
            "kind": "joint_calibration",
            "contract_version": CONTRACT_VERSION,
            "secondary": True,
            "can_adopt": False,
            "estimand": "closing win-share q to frozen PL/Harville joint mapping",
            "known_confounds": [
                "q can be conditionally miscalibrated even when its marginal calibration is good",
                "win marginals do not identify a unique joint finishing-order law",
                "combination pools contain information absent from q",
                "Plackett-Luce IIA and fixed stage lambdas can both be misspecified",
                "closing win odds and combination-grid snapshots can be time-misaligned",
                "selection thresholds were discovered on the original 1,001-race sample",
                "lambda-fit overlap is circular unless the caller supplied only the held-out 334",
                "real-grid low quotes are a market proxy, not ground truth",
                "ordered-bet conclusions extrapolate beyond the unordered pools used for "
                "lambda fit",
            ],
            "limitations": [
                "the frozen input has no grid observation timestamp or split-membership field",
                "the frozen ARMS schema has no devigged real-grid market-proxy arm",
                "floating NaN follows the explicit empty-bin contract and is not strict RFC JSON",
            ],
        },
        "provenance": {
            "preregistration": "docs/plan/prereg-joint-calibration.md",
            "preregistration_revision": 2,
            "n_races": len(races),
            "n_days": len(days),
            "days": {"first": days[0], "last": days[-1]},
            "race_ids": [race.race_id for race in races],
            "arms": list(arms),
            "bootstrap": {"method": "race_day_cluster", "b": b, "seed": seed},
            "bin_edges": list(BIN_EDGES),
            "market_current_lambdas": {
                "lambda2": MARKET_LAMBDA2,
                "lambda3": MARKET_LAMBDA3,
            },
            "real_grid": {
                "race_ids": grid_race_ids,
                "quote_contents_sha256": grid_hash,
                "observed_at": None,
                "split_membership": None,
                "scope": "caller_filtered_unverifiable",
            },
        },
        "stage_losses": _stage_loss_block(races, arms, b=b, seed=seed),
        "bet_type_nll": _nll_block(
            races, arms, distributions, b=b, seed=seed
        ),
        "reliability": _reliability_block(
            races, arms, distributions, b=b, seed=seed
        ),
        "selected_subset": _selected_subset_block(
            races,
            arms,
            distributions,
            selector_distributions,
            b=b,
            seed=seed,
        ),
        "wide_inclusion": _wide_inclusion_block(
            races, arms, distributions, b=b, seed=seed
        ),
        "field_size_mismatch_note": {
            "affected_field_sizes": "5-7",
            "engine_wide_semantics": "pair included in top three",
            "settlement_place_semantics": "top two",
            "diagnostic_action": "wide is excluded below field size 8; other bet types remain",
        },
    }
