"""Synthetic unit tests for the pure pre-registered exotic edge gate."""

from __future__ import annotations

from horseracing_betting.exotic_gate import evaluate_exotic_gate


def _days(values: list[float]) -> dict[str, list[float]]:
    return {f"2026-07-{day:02d}": [value] for day, value in enumerate(values, start=1)}


def test_below_minimum_bets_is_no_decision_despite_positive_diffs():
    diffs = {"exacta": _days([10.0, 10.0, 10.0])}

    verdict = evaluate_exotic_gate(diffs, {"exacta": 4}, b=200, seed=123)["exacta"]

    assert verdict.verdict == "NO_DECISION"
    assert verdict.n_bets == 3
    assert verdict.point_diff == 10.0
    assert verdict.ci_low is None
    assert verdict.ci_high is None
    assert verdict.p_value is None
    assert verdict.p_adjusted is None
    assert "n_bets=3 is below n_min=4" in verdict.note


def test_fewer_than_two_days_is_no_decision():
    diffs = {"trifecta": {"2026-07-01": [2.0, 3.0, 4.0]}}

    verdict = evaluate_exotic_gate(diffs, {"trifecta": 3}, b=200, seed=123)["trifecta"]

    assert verdict.verdict == "NO_DECISION"
    assert verdict.n_bets == 3
    assert verdict.n_days == 1
    assert verdict.ci_low is None
    assert verdict.ci_high is None
    assert verdict.p_value is None
    assert verdict.p_adjusted is None
    assert "n_days=1 is below 2" in verdict.note


def test_strong_positive_edge_is_adopt_candidate():
    diffs = {"trio": _days([1.0, 1.5, 2.0] * 5)}

    verdict = evaluate_exotic_gate(diffs, {"trio": 15}, b=200, seed=123)["trio"]

    assert verdict.verdict == "ADOPT_CANDIDATE"
    assert verdict.n_bets == 15
    assert verdict.n_days == 15
    assert verdict.point_diff > 0.0
    assert verdict.ci_low is not None and verdict.ci_low > 0.0
    assert verdict.p_value is not None and verdict.p_value < verdict.alpha
    assert verdict.p_adjusted is not None and verdict.p_adjusted < verdict.alpha


def test_zero_or_negative_mean_is_rejected():
    diffs = {
        "wide": _days([-1.0, 0.0, -0.5, 0.0] * 3),
        "trifecta": _days([0.0] * 12),
    }

    verdicts = evaluate_exotic_gate(
        diffs,
        {"wide": 12, "trifecta": 12},
        b=200,
        seed=123,
    )

    assert verdicts["wide"].verdict == "REJECT"
    assert verdicts["wide"].point_diff < 0.0
    assert verdicts["trifecta"].verdict == "REJECT"
    assert verdicts["trifecta"].point_diff == 0.0


def test_same_input_and_seed_are_deterministic():
    diffs = {
        "exacta": _days([0.5, 1.0, 1.5, 2.0] * 3),
        "wide": _days([-0.5, 0.0, 0.5, 1.0] * 3),
    }
    n_min = {"exacta": 12, "wide": 12}

    first = evaluate_exotic_gate(diffs, n_min, b=200, seed=20260723)
    second = evaluate_exotic_gate(diffs, n_min, b=200, seed=20260723)

    assert first == second


def test_holm_bonferroni_rejects_marginal_raw_significance():
    diffs = {
        "marginal": _days([1.0] * 12 + [-1.3] * 3),
        "zero": _days([0.0] * 15),
        "negative": _days([-1.0] * 15),
    }

    verdict = evaluate_exotic_gate(
        diffs,
        {bet_type: 15 for bet_type in diffs},
        b=200,
        seed=123,
    )["marginal"]

    assert verdict.p_value is not None and verdict.p_value < verdict.alpha
    assert verdict.ci_low is not None and verdict.ci_low > 0.0
    assert verdict.p_adjusted is not None and verdict.p_adjusted >= verdict.alpha
    assert verdict.verdict == "REJECT"
