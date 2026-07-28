"""Pure top-3 chaos distribution derivation (Feature 084).

Each provenance is derived from one ordered-triple distribution.  The PMF and
all event masses are accumulated together; the triple mass is checked and is
never used to rescale the result.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Literal

from horseracing_eval.baselines import harville_topk
from horseracing_eval.stage_discount import StageDiscount, discounted_topk

from horseracing_probability.chaos_events import EventDefinition
from horseracing_probability.engine import DEFAULT_EPS, joint_probabilities

_MARGINAL_TOL = 1e-12
_BANDS = ("t3_calm", "t3_mild", "t3_mid", "t3_rough", "t3_wild")

ChaosProvenance = Literal["raw", "stage_discount_adjusted"]


class ChaosInvariantError(ValueError):
    """An engine result violated a probability invariant and cannot be displayed."""


@dataclass(frozen=True)
class ChaosDistribution:
    """One provenance-specific distribution and all values derived from it."""

    provenance: ChaosProvenance
    n: int
    support: tuple[int, int]
    pmf: dict[int, float]
    expected_s: float
    event_mass: dict[str, float]
    structural_zero: dict[str, str]
    triple_mass_sum: float


def _validate_inputs(
    q: dict[str, float],
    ranks: dict[str, int],
    events: Sequence[EventDefinition],
    *,
    eps: float,
    invariant_tol: float,
) -> tuple[EventDefinition, ...]:
    if set(q) != set(ranks):
        raise ValueError("q and ranks must contain exactly the same horse ids")
    n = len(q)
    if n < 4:
        raise ValueError("at least four horses are required")

    rank_values = list(ranks.values())
    if any(
        isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0
        for rank in rank_values
    ):
        raise ValueError("popularity ranks must be positive integers without missing values")
    if len(set(rank_values)) != n:
        raise ValueError("popularity ranks must not contain duplicates")

    for horse_id, value in q.items():
        if isinstance(value, bool):
            raise ValueError(f"q[{horse_id!r}] must be a positive finite number")
        try:
            probability = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"q[{horse_id!r}] must be a positive finite number") from exc
        if not math.isfinite(probability) or probability <= 0.0:
            raise ValueError(f"q[{horse_id!r}] must be a positive finite number")

    if not math.isfinite(eps) or not 0.0 < eps < 1.0:
        raise ValueError("eps must be finite and in (0, 1)")
    if not math.isfinite(invariant_tol) or invariant_tol < 0.0:
        raise ValueError("invariant_tol must be finite and non-negative")

    event_tuple = tuple(events)
    event_keys = [event.key for event in event_tuple]
    if len(set(event_keys)) != len(event_keys):
        raise ValueError("event keys must be unique")
    return event_tuple


def _require_close(actual: float, expected: float, *, tol: float, invariant: str) -> None:
    if not math.isfinite(actual) or abs(actual - expected) > tol:
        raise ChaosInvariantError(f"{invariant}: {actual!r} != {expected!r}")


def chaos_distribution(
    q: dict[str, float],
    ranks: dict[str, int],
    events: Sequence[EventDefinition],
    *,
    stage_discount: StageDiscount | None = None,
    eps: float = DEFAULT_EPS,
    invariant_tol: float = 1e-9,
) -> ChaosDistribution:
    """Derive one provenance from the existing ordered-triple engine.

    Popularity gaps are accepted because scratched horses may already have
    consumed rank numbers.  Missing/duplicate ranks and partial q vectors fail
    closed instead of being implicitly reranked or partially normalized.
    """

    event_tuple = _validate_inputs(q, ranks, events, eps=eps, invariant_tol=invariant_tol)
    if stage_discount is not None:
        for name, value in (
            ("lambda2", stage_discount.lambda2),
            ("lambda3", stage_discount.lambda3),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be a positive finite number")

    try:
        result = joint_probabilities(
            q,
            field_size=len(q),
            eps=eps,
            stage_discount=stage_discount,
        )
    except (OverflowError, ZeroDivisionError) as exc:
        raise ChaosInvariantError("ordered-triple engine was numerically unstable") from exc

    n = len(q)
    structural_zero = {
        event.key: (
            f"{event.key} is infeasible for a field of {n}; "
            f"requires at least {event.infeasible_when_n_le + 1} runners"
        )
        for event in event_tuple
        if n <= event.infeasible_when_n_le
    }
    active_events = tuple(event for event in event_tuple if event.key not in structural_zero)

    pmf_terms: dict[int, list[float]] = {}
    event_terms = {event.key: [] for event in active_events}
    first_terms = {horse_id: [] for horse_id in result.win}
    top2_terms = {horse_id: [] for horse_id in result.win}
    top3_terms = {horse_id: [] for horse_id in result.win}
    triple_terms: list[float] = []
    expected_s_terms: list[float] = []

    # C2/FR-010: this is the sole traversal of result.trifecta.  Position-sensitive
    # predicates are evaluated here, before the ordered triples are reduced to S.
    for (first, second, third), mass in result.trifecta.items():
        if not math.isfinite(mass) or mass < 0.0:
            raise ChaosInvariantError(f"invalid ordered-triple mass {mass!r}")
        ra, rb, rc = ranks[first], ranks[second], ranks[third]
        s = ra + rb + rc

        triple_terms.append(mass)
        expected_s_terms.append(s * mass)
        pmf_terms.setdefault(s, []).append(mass)
        first_terms[first].append(mass)
        top2_terms[first].append(mass)
        top2_terms[second].append(mass)
        top3_terms[first].append(mass)
        top3_terms[second].append(mass)
        top3_terms[third].append(mass)
        for event in active_events:
            if event.predicate(ra, rb, rc, n):
                event_terms[event.key].append(mass)

    triple_mass_sum = math.fsum(triple_terms)
    _require_close(
        triple_mass_sum,
        1.0,
        tol=invariant_tol,
        invariant="INV-C1 ordered-triple mass",
    )

    pmf = {s: math.fsum(pmf_terms[s]) for s in sorted(pmf_terms)}
    pmf_mass_sum = math.fsum(pmf.values())
    _require_close(pmf_mass_sum, 1.0, tol=invariant_tol, invariant="INV-C2 S-PMF mass")

    expected_s = math.fsum(expected_s_terms)
    support = (6, 3 * n - 3)
    dense_ranks = set(ranks.values()) == set(range(1, n + 1))
    if dense_ranks and not support[0] - invariant_tol <= expected_s <= support[1] + invariant_tol:
        raise ChaosInvariantError(
            f"INV-C3 expected S {expected_s!r} is outside support {support!r}"
        )

    expected_s_from_pmf = math.fsum(s * mass for s, mass in pmf.items())
    _require_close(
        expected_s,
        expected_s_from_pmf,
        tol=_MARGINAL_TOL,
        invariant="INV-C4 expected S from PMF",
    )

    ids = sorted(result.win)
    normalized_q = [result.win[horse_id] for horse_id in ids]
    first_marginal = {horse_id: math.fsum(first_terms[horse_id]) for horse_id in ids}
    top2_marginal = {horse_id: math.fsum(top2_terms[horse_id]) for horse_id in ids}
    top3_marginal = {horse_id: math.fsum(top3_terms[horse_id]) for horse_id in ids}

    for horse_id in ids:
        _require_close(
            first_marginal[horse_id],
            result.win[horse_id],
            tol=_MARGINAL_TOL,
            invariant=f"INV-C5 first-place marginal for {horse_id}",
        )

    if stage_discount is not None and not stage_discount.is_identity:
        expected_top2, expected_top3 = discounted_topk(normalized_q, stage_discount)
    else:
        expected_top2, expected_top3 = harville_topk(normalized_q)
    for index, horse_id in enumerate(ids):
        _require_close(
            top2_marginal[horse_id],
            expected_top2[index],
            tol=_MARGINAL_TOL,
            invariant=f"INV-C6/C7 top2 marginal for {horse_id}",
        )
        _require_close(
            top3_marginal[horse_id],
            expected_top3[index],
            tol=_MARGINAL_TOL,
            invariant=f"INV-C6/C7 top3 marginal for {horse_id}",
        )

    expected_s_from_top3 = math.fsum(
        ranks[horse_id] * top3_marginal[horse_id] for horse_id in ids
    )
    _require_close(
        expected_s,
        expected_s_from_top3,
        tol=_MARGINAL_TOL,
        invariant="INV-C4 expected S from top3 marginals",
    )

    event_mass = {
        event.key: (
            0.0 if event.key in structural_zero else math.fsum(event_terms[event.key])
        )
        for event in event_tuple
    }
    for event in event_tuple:
        if event.nested_under is None or event.nested_under not in event_mass:
            continue
        if event_mass[event.key] > event_mass[event.nested_under] + invariant_tol:
            raise ChaosInvariantError(
                f"INV-C8 nested event {event.key} exceeds {event.nested_under}"
            )

    provenance: ChaosProvenance = (
        "raw"
        if stage_discount is None or stage_discount.is_identity
        else "stage_discount_adjusted"
    )
    return ChaosDistribution(
        provenance=provenance,
        n=n,
        support=support,
        pmf=pmf,
        expected_s=expected_s,
        event_mass=event_mass,
        structural_zero=structural_zero,
        triple_mass_sum=triple_mass_sum,
    )


def chaos_readout(
    q: dict[str, float],
    ranks: dict[str, int],
    events: Sequence[EventDefinition],
    *,
    stage_discount: StageDiscount,
    edges: Sequence[float],
) -> tuple[ChaosDistribution, ChaosDistribution, str]:
    """Return independently validated raw, adjusted, and adjusted-primary band values."""

    raw = chaos_distribution(q, ranks, events, stage_discount=None)
    adjusted = chaos_distribution(q, ranks, events, stage_discount=stage_discount)
    if adjusted.provenance != "stage_discount_adjusted":
        adjusted = replace(adjusted, provenance="stage_discount_adjusted")

    for event in events:
        if event.lambda_sensitive:
            continue
        _require_close(
            adjusted.event_mass[event.key],
            raw.event_mass[event.key],
            tol=_MARGINAL_TOL,
            invariant=f"INV-C9 lambda-invariant event {event.key}",
        )

    try:
        p_primary = adjusted.event_mass["s_ge_20"]
    except KeyError as exc:
        raise ValueError("events must include s_ge_20 for band assignment") from exc
    return raw, adjusted, band_of(p_primary, edges)


def band_of(p_primary: float, edges: Sequence[float]) -> str:
    """Map P(S>=20) to its five bands; equality stays in the lower band."""

    if not math.isfinite(p_primary) or not 0.0 <= p_primary <= 1.0:
        raise ValueError("p_primary must be a finite probability in [0, 1]")
    edge_tuple = tuple(float(edge) for edge in edges)
    if (
        len(edge_tuple) != 4
        or any(not math.isfinite(edge) for edge in edge_tuple)
        or any(left >= right for left, right in pairwise(edge_tuple))
    ):
        raise ValueError("edges must contain four finite, strictly increasing values")
    for index, edge in enumerate(edge_tuple):
        if p_primary <= edge:
            return _BANDS[index]
    return _BANDS[-1]


__all__ = [
    "ChaosDistribution",
    "ChaosInvariantError",
    "ChaosProvenance",
    "band_of",
    "chaos_distribution",
    "chaos_readout",
]
