from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from horseracing_eval.chaos_lambda import (
    MarketLambdaSample,
    conditional_nll_stage2,
    conditional_nll_stage3,
    fit_chaos_lambda,
)
from horseracing_eval.stage_discount import fit_stage_discount


def _samples() -> list[MarketLambdaSample]:
    # A deterministic mix with interior stage-2/3 optima.
    q_vectors = (
        (0.55, 0.25, 0.13, 0.07),
        (0.46, 0.29, 0.16, 0.09),
        (0.41, 0.31, 0.18, 0.10),
        (0.36, 0.30, 0.21, 0.13),
    )
    finishes = (
        (0, 1, 2),
        (0, 2, 1),
        (1, 0, 2),
        (0, 1, 3),
        (2, 0, 1),
        (1, 2, 0),
    )
    return [
        MarketLambdaSample(win=q_vectors[index % len(q_vectors)], i1=i1, i2=i2, i3=i3)
        for index, (i1, i2, i3) in enumerate(finishes * 8)
    ]


def test_conditional_nll_matches_hand_calculation() -> None:
    sample = MarketLambdaSample(win=(0.5, 0.3, 0.15, 0.05), i1=0, i2=2, i3=1)
    lam = 0.8
    weights = [q**lam for q in sample.win]

    expected_stage2 = -math.log(weights[2] / (sum(weights) - weights[0]))
    expected_stage3 = -math.log(
        weights[1] / (sum(weights) - weights[0] - weights[2])
    )

    assert conditional_nll_stage2(lam, [sample]) == pytest.approx(expected_stage2)
    assert conditional_nll_stage3(lam, [sample]) == pytest.approx(expected_stage3)


def test_fit_is_exactly_the_stage_discount_fit_on_market_q() -> None:
    samples = _samples()

    actual = fit_chaos_lambda(samples, min_races=10)
    expected = fit_stage_discount(samples, min_races=10)

    assert actual == expected
    assert actual.n_races_l2 == len(samples)
    assert actual.n_races_l3 == len(samples)


def test_insufficient_races_fall_back_to_identity() -> None:
    result = fit_chaos_lambda(_samples()[:4], min_races=5)

    assert result.fallback is True
    assert result.is_identity
    assert result.n_races_l2 == 4
    assert result.n_races_l3 == 4


def test_module_does_not_import_probability() -> None:
    path = Path(__file__).parents[2] / "src" / "horseracing_eval" / "chaos_lambda.py"
    tree = ast.parse(path.read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )

    assert all(not name.startswith("horseracing_probability") for name in imported)
