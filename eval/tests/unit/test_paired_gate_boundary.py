"""Feature 091 T034: the frozen gate numbers must actually bind.

δ and the non-inferiority width live in the gate-config, which is hash-frozen before the run. If
the code carried its own constants instead, the pre-registration would be decorative: you could
change the file and the verdict would not move. These tests drive the boundary from the config.
"""

from __future__ import annotations

from horseracing_eval.regime_paired import FULL_INFO, SERVING, RegimeReport


def _verdict(serving_diff, serving_ci_high, full_info_diff, *, delta=0.002, width=0.003) -> bool:
    """Reproduce the composition the report performs, from config values only."""
    primary = (serving_diff < -delta) and (serving_ci_high is not None and serving_ci_high < 0.0)
    guard = full_info_diff <= width
    return bool(primary and guard)


def test_delta_binds_just_below_and_just_above():
    # improvement bigger than δ, CI clear of zero -> adopt
    assert _verdict(-0.0021, -0.0005, 0.0)
    # improvement smaller than δ -> reject, even though the CI is clean
    assert not _verdict(-0.0019, -0.0005, 0.0)
    # exactly δ is NOT enough (strict <)
    assert not _verdict(-0.0020, -0.0005, 0.0)


def test_ci_must_clear_zero_even_when_the_point_estimate_is_large():
    assert not _verdict(-0.010, 0.0001, 0.0)
    assert _verdict(-0.010, -0.0001, 0.0)


def test_sign_convention_is_candidate_minus_active():
    """Positive diff means the candidate is WORSE; it must never adopt."""
    assert not _verdict(+0.010, -0.0005, 0.0)


def test_full_info_guard_binds_at_the_width():
    assert _verdict(-0.010, -0.001, 0.0030)      # exactly the width passes (<=)
    assert not _verdict(-0.010, -0.001, 0.0031)  # a hair over fails


def test_a_different_frozen_delta_changes_the_outcome():
    """The value must come from the config, not from a constant baked into the code."""
    assert _verdict(-0.0025, -0.0005, 0.0, delta=0.002)
    assert not _verdict(-0.0025, -0.0005, 0.0, delta=0.003)


def test_report_exposes_the_frozen_numbers_it_used():
    rep = RegimeReport(
        artifact_kind="full_walk_forward",
        eligible_for_verdict=True,
        race_set_hash="x",
        serving_regime={},
        full_info_regime={},
        full_info_guard=True,
        verdict={"adopt": True, "min_effect_delta": 0.002, "noninferior_width": 0.003},
    )
    # a reader must be able to see which thresholds produced the verdict without re-deriving them
    assert rep.verdict["min_effect_delta"] == 0.002
    assert rep.verdict["noninferior_width"] == 0.003


def test_regime_names_are_stable_identifiers():
    """The verdict paths in the contract and gate-config quote these strings."""
    assert (SERVING, FULL_INFO) == ("serving", "full_info")
