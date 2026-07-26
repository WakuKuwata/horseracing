"""Feature 084: top-3 chaos distribution contract and invariants."""

from __future__ import annotations

import ast
import importlib
import inspect
import math
from dataclasses import replace

import pytest
from horseracing_eval.baselines import harville_topk
from horseracing_eval.stage_discount import StageDiscount, discounted_topk

from horseracing_probability.chaos_distribution import (
    ChaosInvariantError,
    band_of,
    chaos_distribution,
    chaos_readout,
)
from horseracing_probability.chaos_events import (
    CHAOS_EVENTS_V1,
    EventDefinition,
)
from horseracing_probability.engine import joint_probabilities

OPERATIONAL_DISCOUNT = StageDiscount(lambda2=0.8312, lambda3=0.7101)
EDGES = (0.0196, 0.0659, 0.1117, 0.1702)


def _field(n: int) -> tuple[dict[str, float], dict[str, int]]:
    ids = [f"h{index:02d}" for index in range(1, n + 1)]
    q = {horse_id: float(n - index + 1) for index, horse_id in enumerate(ids, start=1)}
    ranks = {horse_id: index for index, horse_id in enumerate(ids, start=1)}
    return q, ranks


def _uniform_field(n: int) -> tuple[dict[str, float], dict[str, int]]:
    ids = [f"h{index:02d}" for index in range(1, n + 1)]
    return (
        {horse_id: 1.0 / n for horse_id in ids},
        {horse_id: index for index, horse_id in enumerate(ids, start=1)},
    )


def _event(key: str) -> EventDefinition:
    return next(event for event in CHAOS_EVENTS_V1 if event.key == key)


def _top_marginals(
    trifecta: dict[tuple[str, str, str], float],
    ids: list[str],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    first = {
        horse_id: math.fsum(mass for trip, mass in trifecta.items() if trip[0] == horse_id)
        for horse_id in ids
    }
    top2 = {
        horse_id: math.fsum(
            mass for trip, mass in trifecta.items() if horse_id in (trip[0], trip[1])
        )
        for horse_id in ids
    }
    top3 = {
        horse_id: math.fsum(mass for trip, mass in trifecta.items() if horse_id in trip)
        for horse_id in ids
    }
    return first, top2, top3


# ---- C1: input validation ----------------------------------------------------


def test_rejects_invalid_ranks():
    q, ranks = _field(4)

    missing = dict(ranks)
    missing.pop("h04")
    with pytest.raises(ValueError):
        chaos_distribution(q, missing, CHAOS_EVENTS_V1)

    duplicate = dict(ranks)
    duplicate["h04"] = duplicate["h03"]
    with pytest.raises(ValueError):
        chaos_distribution(q, duplicate, CHAOS_EVENTS_V1)

    null_rank = dict(ranks)
    null_rank["h04"] = None  # type: ignore[assignment]
    with pytest.raises(ValueError):
        chaos_distribution(q, null_rank, CHAOS_EVENTS_V1)

    partial_q = dict(q)
    partial_q.pop("h04")
    with pytest.raises(ValueError):
        chaos_distribution(partial_q, ranks, CHAOS_EVENTS_V1)


def test_ranks_with_scratch_gap_accepted():
    q, ranks = _field(4)
    ranks["h04"] = 5
    result = chaos_distribution(q, ranks, CHAOS_EVENTS_V1)
    assert max(ranks.values()) > result.n
    assert result.triple_mass_sum == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("bad_value", [0.0, -0.1, math.nan, math.inf])
def test_rejects_nonpositive_or_nonfinite_q(bad_value):
    q, ranks = _field(4)
    q["h04"] = bad_value
    with pytest.raises(ValueError):
        chaos_distribution(q, ranks, CHAOS_EVENTS_V1)


def test_rejects_field_smaller_than_four():
    q, ranks = _field(3)
    with pytest.raises(ValueError):
        chaos_distribution(q, ranks, CHAOS_EVENTS_V1)


# ---- C2/C3: one ordered scan and fail-closed mass ----------------------------


def test_events_need_order():
    himo_are = _event("himo_are").predicate
    total_collapse = _event("total_collapse").predicate

    assert sum((1, 9, 10)) == sum((10, 1, 9)) == 20
    assert himo_are(1, 9, 10, 18) is True
    assert himo_are(10, 1, 9, 18) is False
    assert total_collapse(1, 9, 10, 18) is False
    assert total_collapse(10, 1, 9, 18) is True


@pytest.mark.parametrize("n", range(4, 19))
def test_triple_mass_is_one_operational_lambda(n):
    q, ranks = _field(n)
    result = chaos_distribution(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    assert abs(result.triple_mass_sum - 1.0) <= 1e-9
    assert abs(math.fsum(result.pmf.values()) - 1.0) <= 1e-9


@pytest.mark.parametrize("adversarial_lambda", [1.5, 2.5])
def test_adversarial_lambda_raises(adversarial_lambda):
    q = {"h00": 1.0, **{f"h{index:02d}": 1e-300 for index in range(1, 18)}}
    ranks = {horse_id: index for index, horse_id in enumerate(sorted(q), start=1)}
    discount = StageDiscount(
        lambda2=adversarial_lambda,
        lambda3=adversarial_lambda,
    )
    with pytest.raises(ChaosInvariantError):
        chaos_distribution(q, ranks, CHAOS_EVENTS_V1, stage_discount=discount)


def test_no_global_renormalization(monkeypatch):
    module = importlib.import_module("horseracing_probability.chaos_distribution")
    tree = ast.parse(inspect.getsource(module))
    assert not any(isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) for node in ast.walk(tree))

    q, ranks = _field(4)
    valid = joint_probabilities(q)
    broken = replace(
        valid,
        trifecta={triple: mass * 0.5 for triple, mass in valid.trifecta.items()},
    )
    monkeypatch.setattr(module, "joint_probabilities", lambda *args, **kwargs: broken)
    with pytest.raises(ChaosInvariantError, match="ordered-triple mass"):
        module.chaos_distribution(q, ranks, CHAOS_EVENTS_V1)


def test_trifecta_is_traversed_once():
    module = importlib.import_module("horseracing_probability.chaos_distribution")
    tree = ast.parse(inspect.getsource(module.chaos_distribution))
    trifecta_references = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "trifecta"
    ]
    assert len(trifecta_references) == 1


# ---- INV-C4..C7: independent marginal identities ----------------------------


def test_expected_s_identity():
    q, ranks = _field(12)
    result = chaos_distribution(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    joint = joint_probabilities(q, stage_discount=OPERATIONAL_DISCOUNT)
    ids = sorted(q)
    _, _, top3 = _top_marginals(joint.trifecta, ids)
    expected_from_top3 = math.fsum(ranks[horse_id] * top3[horse_id] for horse_id in ids)
    assert result.expected_s == pytest.approx(expected_from_top3, abs=1e-12)
    assert result.expected_s == pytest.approx(
        math.fsum(s * mass for s, mass in result.pmf.items()),
        abs=1e-12,
    )


@pytest.mark.parametrize(
    "discount",
    [
        StageDiscount(lambda2=0.8312, lambda3=0.7101),
        StageDiscount(lambda2=0.55, lambda3=1.1),
    ],
)
def test_first_place_marginal_equals_q(discount):
    q, ranks = _field(10)
    chaos_distribution(q, ranks, CHAOS_EVENTS_V1, stage_discount=discount)
    joint = joint_probabilities(q, stage_discount=discount)
    ids = sorted(q)
    first, _, _ = _top_marginals(joint.trifecta, ids)
    for horse_id in ids:
        assert first[horse_id] == pytest.approx(joint.win[horse_id], abs=1e-12)


def test_topk_marginals_match_049():
    q, ranks = _field(14)
    chaos_distribution(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    joint = joint_probabilities(q, stage_discount=OPERATIONAL_DISCOUNT)
    ids = sorted(q)
    normalized_q = [joint.win[horse_id] for horse_id in ids]
    expected_top2, expected_top3 = discounted_topk(normalized_q, OPERATIONAL_DISCOUNT)
    _, actual_top2, actual_top3 = _top_marginals(joint.trifecta, ids)
    for index, horse_id in enumerate(ids):
        assert actual_top2[horse_id] == pytest.approx(expected_top2[index], abs=1e-12)
        assert actual_top3[horse_id] == pytest.approx(expected_top3[index], abs=1e-12)


def test_lambda_one_matches_harville():
    q, ranks = _field(11)
    chaos_distribution(q, ranks, CHAOS_EVENTS_V1, stage_discount=StageDiscount())
    joint = joint_probabilities(q)
    ids = sorted(q)
    normalized_q = [joint.win[horse_id] for horse_id in ids]
    expected_top2, expected_top3 = harville_topk(normalized_q)
    _, actual_top2, actual_top3 = _top_marginals(joint.trifecta, ids)
    for index, horse_id in enumerate(ids):
        assert actual_top2[horse_id] == pytest.approx(expected_top2[index], abs=1e-12)
        assert actual_top3[horse_id] == pytest.approx(expected_top3[index], abs=1e-12)


def test_total_collapse_lambda_invariant():
    q, ranks = _field(18)
    raw, adjusted, _ = chaos_readout(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
        edges=EDGES,
    )
    assert raw.event_mass["total_collapse"] == pytest.approx(
        adjusted.event_mass["total_collapse"],
        abs=1e-12,
    )


# ---- C5/INV-C8: structural zeros and only declared nesting -------------------


def test_structural_zero_is_zero_not_none():
    q7, ranks7 = _uniform_field(7)
    seven = chaos_distribution(q7, ranks7, CHAOS_EVENTS_V1)
    assert seven.event_mass["s_ge_20"] == 0.0
    assert seven.event_mass["s_ge_20"] is not None
    assert "s_ge_20" in seven.structural_zero

    q8, ranks8 = _uniform_field(8)
    eight = chaos_distribution(q8, ranks8, CHAOS_EVENTS_V1)
    assert eight.event_mass["s_ge_20"] > 0.0
    assert "s_ge_20" not in eight.structural_zero

    q9, ranks9 = _uniform_field(9)
    nine = chaos_distribution(q9, ranks9, CHAOS_EVENTS_V1)
    assert nine.event_mass["himo_are"] == 0.0
    assert nine.event_mass["total_collapse"] == 0.0
    assert {"himo_are", "total_collapse"} <= set(nine.structural_zero)

    q10, ranks10 = _uniform_field(10)
    ten = chaos_distribution(q10, ranks10, CHAOS_EVENTS_V1)
    assert ten.event_mass["s_ge_30"] == 0.0
    assert "s_ge_30" in ten.structural_zero


def test_support_bounds():
    for n in (4, 8, 18):
        q, ranks = _field(n)
        result = chaos_distribution(q, ranks, CHAOS_EVENTS_V1)
        assert result.support == (6, 3 * n - 3)
        assert all(result.support[0] <= s <= result.support[1] for s in result.pmf)
        assert result.support[0] <= result.expected_s <= result.support[1]


def test_nested_events_monotone():
    q, ranks = _field(18)
    result = chaos_distribution(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    assert result.event_mass["s_ge_30"] <= result.event_mass["s_ge_20"]


def test_non_nested_not_forced():
    ids = [f"h{rank:02d}" for rank in range(1, 11)]
    ranks = {horse_id: rank for rank, horse_id in enumerate(ids, start=1)}
    q = {horse_id: 0.00625 for horse_id in ids}
    q["h01"] = 0.85
    q["h10"] = 0.1
    result = chaos_distribution(q, ranks, CHAOS_EVENTS_V1)
    assert _event("himo_are").nested_under is None
    assert result.event_mass["himo_are"] > result.event_mass["s_ge_20"]


# ---- INV-C11/C7 and normative event definitions ------------------------------


def test_events_match_normative_predicates():
    s_ge_20 = _event("s_ge_20").predicate
    s_ge_30 = _event("s_ge_30").predicate
    himo_are = _event("himo_are").predicate
    total_collapse = _event("total_collapse").predicate

    assert s_ge_20(1, 9, 10, 18) is True
    assert s_ge_20(1, 8, 10, 18) is False
    assert s_ge_30(10, 9, 11, 18) is True
    assert s_ge_30(10, 9, 10, 18) is False
    assert himo_are(1, 10, 5, 18) is True
    assert himo_are(1, 5, 10, 18) is True
    assert himo_are(1, 5, 6, 18) is False
    assert himo_are(4, 10, 11, 18) is False
    assert total_collapse(10, 1, 2, 18) is True
    assert total_collapse(9, 10, 11, 18) is False

    assert _event("s_ge_30").nested_under == "s_ge_20"
    assert _event("total_collapse").lambda_sensitive is False
    assert _event("s_ge_20").infeasible_when_n_le == 7
    assert _event("s_ge_30").infeasible_when_n_le == 10
    assert _event("himo_are").infeasible_when_n_le == 9
    assert _event("total_collapse").infeasible_when_n_le == 9


def test_uniform_field_uniform_triples():
    q, ranks = _uniform_field(8)
    result = chaos_distribution(q, ranks, CHAOS_EVENTS_V1)
    joint = joint_probabilities(q)
    masses = tuple(joint.trifecta.values())
    assert len(masses) == 8 * 7 * 6
    assert max(masses) - min(masses) <= 1e-18
    assert result.triple_mass_sum == 1.0


def test_permutation_equivariance():
    q, ranks = _field(8)
    original = chaos_distribution(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    rename = {
        horse_id: f"renamed-{index:02d}"
        for index, horse_id in enumerate(reversed(tuple(q)), start=1)
    }
    renamed_q = {rename[horse_id]: value for horse_id, value in q.items()}
    renamed_ranks = {rename[horse_id]: rank for horse_id, rank in ranks.items()}
    permuted = chaos_distribution(
        renamed_q,
        renamed_ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    assert permuted.pmf == pytest.approx(original.pmf, abs=1e-12)
    assert permuted.event_mass == pytest.approx(original.event_mass, abs=1e-12)
    assert permuted.expected_s == pytest.approx(original.expected_s, abs=1e-12)


def test_uniform_eight_expected_s():
    q, ranks = _uniform_field(8)
    result = chaos_distribution(q, ranks, CHAOS_EVENTS_V1)
    assert result.expected_s == 13.5


def test_deterministic():
    q, ranks = _field(12)
    first = chaos_distribution(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    reversed_q = dict(reversed(tuple(q.items())))
    reversed_ranks = dict(reversed(tuple(ranks.items())))
    second = chaos_distribution(
        reversed_q,
        reversed_ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
    )
    assert first == second
    assert repr(first) == repr(second)


# ---- C4 and FR-018: two provenances and band axis ----------------------------


def test_readout_returns_two_independent_provenances():
    q, ranks = _field(14)
    raw, adjusted, band = chaos_readout(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=OPERATIONAL_DISCOUNT,
        edges=EDGES,
    )
    assert raw.provenance == "raw"
    assert adjusted.provenance == "stage_discount_adjusted"
    assert raw is not adjusted
    assert abs(raw.triple_mass_sum - 1.0) <= 1e-9
    assert abs(adjusted.triple_mass_sum - 1.0) <= 1e-9
    assert band == band_of(adjusted.event_mass["s_ge_20"], EDGES)


@pytest.mark.parametrize(
    ("p_primary", "expected"),
    [
        (0.0, "t3_calm"),
        (0.1, "t3_calm"),
        (0.1000001, "t3_mild"),
        (0.2, "t3_mild"),
        (0.3, "t3_mid"),
        (0.4, "t3_rough"),
        (1.0, "t3_wild"),
    ],
)
def test_band_of_uses_lower_band_for_equal_edge(p_primary, expected):
    assert band_of(p_primary, (0.1, 0.2, 0.3, 0.4)) == expected


def test_band_of_rejects_invalid_edges_and_probability():
    with pytest.raises(ValueError):
        band_of(math.nan, EDGES)
    with pytest.raises(ValueError):
        band_of(0.1, (0.1, 0.1, 0.2, 0.3))
    with pytest.raises(ValueError):
        band_of(0.1, (0.1, 0.2, 0.3))
