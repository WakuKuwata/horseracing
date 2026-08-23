from __future__ import annotations

import pytest

from horseracing_training.spelling_gate import (
    FORMULA,
    leave_one_out_points,
    point_estimate,
    pool_diffs_by_day,
    transportability,
    verdict_precedence,
)


def test_transportability_passes_all_three_rules() -> None:
    result = transportability(
        [-0.003, -0.002, -0.001],
        [-0.0025, -0.002, -0.0015],
        pooled_point=-0.002,
        real_ci_low=-0.001,
    )

    assert result == {
        "per_cutoff_sign_ok": True,
        "loo_sign_ok": True,
        "real_not_contradicting": True,
        "ok": True,
    }


@pytest.mark.parametrize(
    ("per_cutoff", "loo", "real_ci_low", "failed_rule"),
    [
        ([-0.003, 0.001, -0.001], [-0.002, -0.001, -0.003], -0.001,
         "per_cutoff_sign_ok"),
        ([-0.003, -0.002, -0.001], [-0.002, 0.001, -0.003], -0.001, "loo_sign_ok"),
        ([-0.003, -0.002, -0.001], [-0.002, -0.001, -0.003], 0.0001,
         "real_not_contradicting"),
    ],
)
def test_transportability_fails_each_frozen_rule(
    per_cutoff: list[float],
    loo: list[float],
    real_ci_low: float,
    failed_rule: str,
) -> None:
    result = transportability(
        per_cutoff,
        loo,
        pooled_point=-0.002,
        real_ci_low=real_ci_low,
    )

    assert result[failed_rule] is False
    assert result["ok"] is False


def test_transportability_real_rule_is_vacuous_for_nonnegative_pool() -> None:
    result = transportability(
        [0.001, 0.002], [0.0015, 0.0015], pooled_point=0.0015, real_ci_low=None
    )

    assert result["real_not_contradicting"] is True
    assert result["ok"] is True


@pytest.mark.parametrize(
    ("inputs", "status", "cause"),
    [
        (
            dict(
                runnable=False,
                sufficient=True,
                primary_pooled=True,
                guard_real_direction=True,
                transportable=True,
            ),
            "NO_DECISION",
            "not_runnable",
        ),
        (
            dict(
                runnable=True,
                sufficient=False,
                primary_pooled=True,
                guard_real_direction=True,
                transportable=True,
            ),
            "NO_DECISION",
            "insufficient_eval_days",
        ),
        (
            dict(
                runnable=True,
                sufficient=True,
                primary_pooled=False,
                guard_real_direction=True,
                transportable=False,
            ),
            "REJECT",
            "primary_pooled AND guard_real_direction not both true",
        ),
        (
            dict(
                runnable=True,
                sufficient=True,
                primary_pooled=True,
                guard_real_direction=True,
                transportable=False,
            ),
            "NO_DECISION",
            "transportability_failed",
        ),
        (
            dict(
                runnable=True,
                sufficient=True,
                primary_pooled=True,
                guard_real_direction=True,
                transportable=True,
            ),
            "ADOPT",
            FORMULA,
        ),
    ],
)
def test_verdict_precedence(
    inputs: dict[str, bool], status: str, cause: str
) -> None:
    verdict = verdict_precedence(**inputs)

    assert verdict["status"] == status
    assert verdict["adopt"] is (status == "ADOPT")
    assert verdict["formula"] == FORMULA
    assert verdict["decision_reason"]["cause"] == cause
    assert verdict["decision_reason"]["transportability"] is inputs["transportable"]


def test_pool_diffs_by_day_unions_disjoint_days_and_copies_values() -> None:
    first = {"2020-01-01": [-0.3, -0.1]}
    second = {"2022-01-01": [0.2]}

    pooled = pool_diffs_by_day([first, second])

    assert pooled == {"2020-01-01": [-0.3, -0.1], "2022-01-01": [0.2]}
    assert point_estimate(pooled) == pytest.approx(-0.2 / 3)
    first["2020-01-01"].append(99.0)
    assert pooled["2020-01-01"] == [-0.3, -0.1]


def test_pool_diffs_by_day_rejects_overlapping_days() -> None:
    with pytest.raises(AssertionError, match="double count"):
        pool_diffs_by_day([{"2020-01-01": [-0.1]}, {"2020-01-01": [-0.2]}])


def test_leave_one_out_points_are_race_weighted() -> None:
    parts = [
        {"2020-01-01": [-0.3, -0.1]},
        {"2022-01-01": [0.2]},
        {"2024-01-01": [-0.2]},
    ]

    assert leave_one_out_points(parts) == pytest.approx([0.0, -0.2, -0.2 / 3])
