"""Feature 091 T049/T053: acceptance and diagnostic runs must not be able to decide anything.

Their folds sit inside the confirmatory window, so an effect read off them is a selection leak.
The guard is structural (artifact_kind), and tampering with their NUMBERS must change nothing.
"""

from __future__ import annotations

import pytest

from horseracing_eval.decision import (
    VERDICT_ARTIFACT_KIND,
    VerdictSourceError,
    assert_verdict_eligible,
)


def _report(kind, *, eligible=None, adopt=True, diff=-0.02):
    return {
        "artifact_kind": kind,
        "eligible_for_verdict": (kind == VERDICT_ARTIFACT_KIND) if eligible is None else eligible,
        "serving_regime": {"diff": diff, "ci_high": -0.01},
        "verdict": {"adopt": adopt},
    }


def test_full_walk_forward_is_accepted():
    assert_verdict_eligible(_report(VERDICT_ARTIFACT_KIND))


@pytest.mark.parametrize("kind", ["acceptance", "diagnostic", "screening", None, ""])
def test_other_kinds_are_refused(kind):
    with pytest.raises(VerdictSourceError):
        assert_verdict_eligible(_report(kind))


def test_eligible_flag_alone_cannot_promote_a_diagnostic():
    """Both fields must agree; forging one is not enough."""
    with pytest.raises(VerdictSourceError):
        assert_verdict_eligible(_report("diagnostic", eligible=True))


def test_correct_kind_with_eligible_false_is_refused():
    with pytest.raises(VerdictSourceError):
        assert_verdict_eligible(_report(VERDICT_ARTIFACT_KIND, eligible=False))


@pytest.mark.parametrize("tampered_diff", [-99.0, 0.0, +99.0])
def test_tampering_with_diagnostic_numbers_does_not_make_them_usable(tampered_diff):
    """The isolation is by KIND, not by value — no diagnostic number can ever reach a verdict."""
    with pytest.raises(VerdictSourceError):
        assert_verdict_eligible(_report("diagnostic", diff=tampered_diff, adopt=True))
