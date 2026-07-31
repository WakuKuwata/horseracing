"""Contract tests for the pre-registered joint-calibration instrument.

These tests are intentionally derived from the frozen contract and rev2 pre-registration.  This
file covers only predictor-agnostic functions and the pure uniform/normalized-independent arms;
009-engine integration lives in training, which owns both dependency workspaces.
"""

from __future__ import annotations

import math
from itertools import permutations

import pytest

from horseracing_eval.joint_calibration import (
    BIN_EDGES,
    JointCalibRace,
    JointCalibrationError,
    bet_type_distributions,
    normalized_independent,
    realized_keys,
    stage_losses,
)

_Q_A = (0.40, 0.20, 0.13, 0.10, 0.07, 0.05, 0.03, 0.02)


def _race(
    n: int = 8,
    *,
    q=None,
    top3=(1, 2, 3),
    race_id="r1",
    day="2026-01-01",
    grid=None,
):
    numbers = tuple(range(1, n + 1))
    if q is None:
        q = (1.0 / n,) * n
    return JointCalibRace(race_id, day, numbers, tuple(q), tuple(top3), grid)


# --- probability mass and settlement ------------------------------------------------------------


def test_uniform_n8_has_unit_categorical_mass_and_wide_mass_three():
    """Uniform N=8 must give four categorical distributions of mass one but wide mass three;
    normalizing wide to one would understate every inclusion probability by exactly threefold."""
    got = bet_type_distributions(_race(), arm="uniform")
    expected_sizes = {"exacta": 56, "quinella": 28, "trio": 56, "trifecta": 336}

    assert set(got) == {*expected_sizes, "wide"}
    for bet_type, size in expected_sizes.items():
        assert len(got[bet_type]) == size
        assert sum(got[bet_type].values()) == pytest.approx(1.0, abs=1e-14)
        assert all(
            probability == pytest.approx(1.0 / size, abs=1e-15)
            for probability in got[bet_type].values()
        )

    assert len(got["wide"]) == math.comb(8, 2)
    assert all(
        probability == pytest.approx(3.0 / math.comb(8, 2), abs=1e-15)
        for probability in got["wide"].values()
    )
    assert sum(got["wide"].values()) == pytest.approx(3.0, abs=1e-14)


def test_normalized_independent_is_normalized_and_has_factorial_coefficients():
    """The independent baseline must normalize over distinct runners and multiply unordered
    pairs/triples by 2/6; omitting either step creates a non-probability baseline."""
    numbers = tuple(range(1, len(_Q_A) + 1))
    ordered2 = normalized_independent(_Q_A, numbers, 2, ordered=True)
    unordered2 = normalized_independent(_Q_A, numbers, 2, ordered=False)
    ordered3 = normalized_independent(_Q_A, numbers, 3, ordered=True)
    unordered3 = normalized_independent(_Q_A, numbers, 3, ordered=False)

    assert len(ordered2) == math.perm(8, 2) and len(unordered2) == math.comb(8, 2)
    assert len(ordered3) == math.perm(8, 3) and len(unordered3) == math.comb(8, 3)
    for distribution in (ordered2, unordered2, ordered3, unordered3):
        assert sum(distribution.values()) == pytest.approx(1.0, abs=1e-14)

    for key, probability in unordered2.items():
        assert key == tuple(sorted(key))
        assert probability == pytest.approx(2.0 * ordered2[key], rel=1e-14, abs=1e-15)
    for key, probability in unordered3.items():
        assert key == tuple(sorted(key))
        assert probability == pytest.approx(6.0 * ordered3[key], rel=1e-14, abs=1e-15)

    z2 = sum(_Q_A[i] * _Q_A[j] for i, j in permutations(range(8), 2))
    z3 = sum(_Q_A[i] * _Q_A[j] * _Q_A[k] for i, j, k in permutations(range(8), 3))
    assert unordered2[(1, 4)] == pytest.approx(2 * _Q_A[0] * _Q_A[3] / z2, abs=1e-15)
    assert unordered3[(1, 4, 7)] == pytest.approx(
        6 * _Q_A[0] * _Q_A[3] * _Q_A[6] / z3, abs=1e-15
    )


def test_realized_keys_have_one_categorical_positive_and_three_wide_positives():
    """Categorical tickets have exactly one positive while N>=8 wide has three canonical
    positives; extra or missing positives change the reliability denominator's meaning."""
    race = _race(top3=(7, 2, 5))
    expected = {
        "exacta": ((7, 2),),
        "quinella": ((2, 7),),
        "trio": ((2, 5, 7),),
        "trifecta": ((7, 2, 5),),
        "wide": ((2, 5), (2, 7), (5, 7)),
    }
    distributions = bet_type_distributions(race, arm="uniform")

    for bet_type, keys in expected.items():
        got = realized_keys(bet_type, race.top3)
        if bet_type == "wide":
            assert set(got) == set(keys)
        else:
            assert got == keys
        assert sum(key in keys for key in distributions[bet_type]) == len(keys)
    assert all(len(expected[bet_type]) == 1 for bet_type in ("exacta", "quinella", "trio", "trifecta"))
    assert len(expected["wide"]) == 3


# --- reliability partition and estimands --------------------------------------------------------


def test_bin_edges_form_the_frozen_partition_of_zero_to_one():
    """The frozen logarithmic bins must cover [0,1] once with no gaps or overlap, because a
    missing boundary cell can make tail overconfidence disappear from the readout."""
    assert BIN_EDGES == (1e-6, 1e-5, 1e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1, 3e-1, 1.0)
    assert all(
        left < right
        for left, right in zip((0.0, *BIN_EDGES[:-1]), BIN_EDGES, strict=True)
    )
    assert BIN_EDGES[-1] == 1.0

    intervals = list(zip((0.0, *BIN_EDGES[:-1]), BIN_EDGES, strict=True))
    probes = {0.0, 1.0}
    for boundary in BIN_EDGES[:-1]:
        probes.update((math.nextafter(boundary, 0.0), boundary, math.nextafter(boundary, 1.0)))
    for value in probes:
        memberships = [
            lower <= value < upper or (idx == len(intervals) - 1 and value == upper)
            for idx, (lower, upper) in enumerate(intervals)
        ]
        assert sum(memberships) == 1, value


# --- eligibility, leak boundary, and frozen arms ------------------------------------------------


def test_top3_tie_is_rejected_and_lower_ranks_are_outside_scoring_contract():
    """A top-three tie must fail because the winning ticket is ambiguous, while lower-rank ties
    must not affect scoring because JointCalibRace deliberately retains only the unique top three."""
    with pytest.raises(JointCalibrationError):
        tied = _race(top3=(1, 1, 3))
        stage_losses(tied, lambda2=1.0, lambda3=1.0)

    # Fourth and lower ranks are intentionally absent from the scoring contract, so a valid top3
    # remains scoreable regardless of any hypothetical tie below it.
    valid = _race(top3=(1, 2, 3))
    assert all(math.isfinite(loss) for loss in stage_losses(valid, lambda2=1.0, lambda3=1.0))


# --- public input validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    (
        {"numbers": (1, 2, 3, 4), "q": (0.4, 0.3, 0.3)},
        {"numbers": (1, 3, 2, 4), "q": (0.4, 0.3, 0.2, 0.1)},
        {"numbers": (1, 2, 2, 4), "q": (0.4, 0.3, 0.2, 0.1)},
        {"q": (0.4, 0.3, 0.2, 0.2)},
        {"q": (0.5, 0.3, 0.2, 0.0)},
        {"q": (0.6, 0.3, 0.2, -0.1)},
        {"q": (0.4, 0.3, 0.2, math.nan)},
        {"q": (0.4, 0.3, 0.2, math.inf)},
        {"top3": (1, 1, 3)},
        {"top3": (1, 2, 9)},
    ),
    ids=(
        "length-mismatch",
        "numbers-unsorted",
        "numbers-duplicate",
        "q-sum",
        "q-zero",
        "q-negative",
        "q-nan",
        "q-inf",
        "top3-duplicate",
        "top3-not-started",
    ),
)
def test_invalid_joint_calib_race_fails_with_domain_error(overrides):
    """Every frozen JointCalibRace invariant must fail with JointCalibrationError rather than a
    generic arithmetic error, because partial or malformed races must never enter the instrument."""
    values = {
        "race_id": "bad",
        "day": "2026-01-01",
        "numbers": (1, 2, 3, 4),
        "q": (0.4, 0.3, 0.2, 0.1),
        "top3": (1, 2, 3),
        "grid": None,
    }
    values.update(overrides)
    with pytest.raises(JointCalibrationError):
        race = JointCalibRace(**values)
        stage_losses(race, lambda2=1.0, lambda3=1.0)
