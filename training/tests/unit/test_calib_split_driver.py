"""T027: calib-split driver screening go/no-go + arm construction (FR-010/FR-014)."""

from __future__ import annotations

from horseracing_training.calib_split_eval import _screen_decision, default_arms


class _Rep:
    def __init__(self, diff, lo, hi, no_decision=False):
        self.periods = {"all": {"diff": diff}}
        self.bootstrap_ci = {
            "ci_low": lo, "ci_high": hi, "no_decision": no_decision,
        }


def test_arms_share_objective_and_differ_only_in_calibration():
    arms = default_arms("pl_topk")
    assert [a.name for a in arms] == ["A", "B", "C/D"]
    assert all(a.spec.startswith("pl_topk:") for a in arms)
    assert arms[0].spec == "pl_topk:isotonic:0.3"
    assert arms[1].spec == "pl_topk:isotonic:0.1"
    assert arms[2].spec == "pl_topk:oof_power"


def test_non_inferior_candidate_is_go():
    go, _ = _screen_decision(_Rep(diff=-0.01, lo=-0.02, hi=0.0), margin=0.0)
    assert go is True


def test_clearly_worse_candidate_is_no_go():
    # CI low bound well above the margin -> confidently worse -> drop
    go, reason = _screen_decision(_Rep(diff=0.05, lo=0.02, hi=0.08), margin=0.0)
    assert go is False
    assert "clearly worse" in reason


def test_ci_straddles_zero_promotes_to_confirm():
    go, reason = _screen_decision(_Rep(diff=0.005, lo=-0.01, hi=0.02), margin=0.0)
    assert go is True


def test_no_decision_promotes_to_confirm():
    go, reason = _screen_decision(_Rep(diff=0.0, lo=None, hi=None, no_decision=True), margin=0.0)
    assert go is True
    assert "no_decision" in reason
