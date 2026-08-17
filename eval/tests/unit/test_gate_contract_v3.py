"""Evaluation contract v3: the four gate corrections, pinned against the recorded history.

The motivating measurement (2026-08-17): reproducing the full pre-registered formula
``gate.adopted AND subgroup_guard`` from the 088 record gave an ADOPT probability of 0.24 for a
candidate that truly improves winner NLL by 0.002, and 0.006 under the null — i.e. the gate was
operating at roughly a quarter of its advertised error rate and rejecting three of every four
genuine improvements. The dominant term was NOT the primary CI; it was the subgroup guard, whose
CI half-width (0.0071 on ``2026_only``) exceeded its own margin (0.005), so "non-inferiority" could
only be concluded by proving superiority on 66 race-days.

These tests pin the corrections AND the requirement that no recorded verdict silently flips.
"""

from __future__ import annotations

import datetime

from horseracing_eval.decision import ADOPT, NO_DECISION, REJECT, final_decision
from horseracing_eval.gates import (
    MODE_LEGACY_POINT,
    evaluate_core_gate,
    recent_window_guard,
)
from horseracing_eval.paired import GateResult, _build_gate
from horseracing_eval.subgroups import (
    INCONCLUSIVE_LOW_PRECISION as LOWP,
)
from horseracing_eval.subgroups import (
    subgroup_guard_status,
    three_way,
)

CFG = {
    "evaluation_contract_version": "v3",
    "eval_window": {"from": "2019-01-01", "to": "2026-08-09", "min_eval_days": 10},
    "subgroup_guard": {
        "critical_subgroups": ["2026_only", "nk", "2026_nk"],
        "non_inferior_margin_winner_nll": 0.005,
        "non_inferior_margin_horse_logloss": 0.001,
        "no_decision_min_days": 10,
    },
    "bootstrap": {"b": 400, "seed": 20260817, "alpha": 0.05},
}


def _days(start: str, values_per_day: list[list[float]]) -> dict:
    d0 = datetime.date.fromisoformat(start)
    return {
        (d0 + datetime.timedelta(days=7 * i)).isoformat(): vals
        for i, vals in enumerate(values_per_day)
    }


# --- fix 2: the recent-window guard is a test, not a sign check -------------------------------

def _noisy_days(n_days=120, mean=0.00091, sd=0.05, seed=0):
    """Per-day paired diffs with real day-to-day variance and a realized mean pinned to +0.00091 —
    088's recorded recent_3y difference, the exact shape v2 called a hard failure."""
    import numpy as np

    rng = np.random.default_rng(seed)
    raw = rng.normal(0.0, sd, (n_days, 4))
    raw = raw - raw.mean() + mean  # pin the realized point estimate, keep the day-to-day spread
    return _days("2024-01-06", [list(row) for row in raw])


def test_recent_guard_does_not_fail_on_noise_that_v2_would_have_rejected():
    """A recent window whose point estimate is barely positive but whose CI is wide is NOT
    evidence of harm. v2 called this a hard failure and decision.py mapped it to REJECT."""
    res = recent_window_guard(_noisy_days(), cfg=CFG)
    assert res["pass"] is True
    assert res["mode"] == "non_inferiority"
    assert all(w.get("decision") != "FAIL" for w in res["windows"].values())
    # the descriptive v2 signal is still reported, now clearly marked as descriptive
    assert res["windows"]["recent_3y"]["point_estimate_degraded"] is True


def test_recent_guard_still_fails_on_a_real_recent_degradation():
    """The guard must keep biting when the recent window is CONFIDENTLY worse than the margin."""
    vals = [[0.02, 0.021, 0.019, 0.02] for _ in range(120)]  # +0.02 >> margin 0.005, tight
    res = recent_window_guard(_days("2024-01-06", vals), cfg=CFG)
    assert res["pass"] is False
    assert res["windows"]["recent_3y"]["decision"] == "FAIL"


def test_legacy_point_estimate_mode_reproduces_v2_verbatim():
    """Reproducing a v1/v2 verdict stays possible, but only by naming the old rule."""
    cfg = {**CFG, "recent_guard": {"mode": MODE_LEGACY_POINT}}
    days = _noisy_days()  # SAME data the v3 guard passes above
    res = recent_window_guard(days, cfg=cfg)
    assert res["mode"] == MODE_LEGACY_POINT
    assert res["pass"] is False  # v2: point estimate > 0 -> hard fail -> REJECT
    assert res["windows"]["recent_3y"]["degraded"] is True


def test_recent_guard_windows_are_nested_and_share_the_primary_bootstrap_settings():
    vals = [[-0.01, -0.009] for _ in range(150)]
    res = recent_window_guard(_days("2023-01-06", vals), cfg=CFG)
    w3, w5 = res["windows"]["recent_3y"], res["windows"]["recent_5y"]
    assert w3["n_races"] <= w5["n_races"]  # 3y ⊂ 5y
    assert w3["decision"] == w5["decision"] == "PASS"


def test_recent_guard_survives_a_leap_day_window_edge():
    """``date(2028, 2, 29).replace(year=2025)`` raises; v2 would have crashed here."""
    res = recent_window_guard(
        {"2028-02-29": [-0.01], "2027-06-01": [-0.01]},
        cfg=CFG, max_date=datetime.date(2028, 2, 29),
    )
    assert res["pass"] is True


# --- fix 1: power-aware subgroup states, veto only on evidence of harm -------------------------

def test_untestable_subgroup_no_longer_looks_like_a_failing_one():
    """088's recorded ``2026_only``: 66 race-days, CI [-0.006027, +0.008261], margin 0.005.
    PASS required point < 0.005 - 0.00714 = -0.0021 — superiority, on 1/12 of the data."""
    state = three_way(-0.006027, 0.008261, 0.005, point=0.001129)
    assert state == LOWP
    status = subgroup_guard_status(
        {"2026_only": state, "nk": LOWP, "2026_nk": LOWP},
        ["2026_only", "nk", "2026_nk"],
    )
    assert status == "NOT_PROVEN"  # not "FAIL": nothing here is evidence against the candidate


# --- historical replay: no recorded verdict may silently flip ---------------------------------

def test_088_becomes_no_decision_instead_of_reject():
    """088 (finish-rank decomposition): point estimate -0.00055 FAVOURED the candidate, the CI
    straddled zero, and all three critical subgroups were untestable. v2 reported REJECT via the
    frozen formula. The honest label is "the instrument could not tell"."""
    # recent_guard: recent_3y diff was +0.00091 with a window SE of ~0.0014, so its CI lower bound
    # sits far below the 0.005 margin -> not confidently worse -> no FAIL.
    gate = GateResult(
        primary=True, stat_guard=False, recent_guard=True, top_noninferior=True,
        calibration=True, adopted=False, reasons={"winner_nll_diff": -0.000551},
    )
    recorded = {  # (ci_low, ci_high, margin) as recorded in artifacts/088_paired_report.json
        "2026_only": (-0.006027, 0.008261, 0.005),
        "nk": (-0.000504, 0.001010, 0.001),
        "2026_nk": (-0.000614, 0.001108, 0.001),
    }
    states = {k: three_way(lo, hi, m) for k, (lo, hi, m) in recorded.items()}
    # 2026_only could never have concluded at its margin; the two horse-level ones could have but
    # sat on the margin. Neither is evidence of harm — v2 treated both as a failing guard.
    assert states["2026_only"] == LOWP
    assert states["nk"] == states["2026_nk"] == "NO_DECISION"
    sg = {
        "subgroup_decisions": states,
        "subgroup_guard": False,
        "subgroup_guard_status": subgroup_guard_status(states, list(recorded)),
    }
    decision, reason = final_decision(gate, sg, n_days=821, cfg=CFG)
    assert decision == NO_DECISION
    assert reason["cause"] == "stat_guard_underpowered"  # not "critical_subgroup_*", not REJECT


def test_091_still_adopts_with_full_subgroup_assurance():
    """091 (serving weight regime, -0.0106) must be unaffected: every subgroup CONCLUDED PASS,
    which stays valid at any CI width."""
    recorded = {
        "2026_only": (-0.02041, -0.00471, 0.005),
        "nk": (-0.00147, 0.00002, 0.001),
        "2026_nk": (-0.00181, -0.00015, 0.001),
    }
    states = {k: three_way(lo, hi, m) for k, (lo, hi, m) in recorded.items()}
    assert set(states.values()) == {"PASS"}
    sg = {
        "subgroup_decisions": states, "subgroup_guard": True,
        "subgroup_guard_status": subgroup_guard_status(states, list(recorded)),
    }
    gate = GateResult(
        primary=True, stat_guard=True, recent_guard=True, top_noninferior=True,
        calibration=True, adopted=True, reasons={"winner_nll_diff": -0.010592},
    )
    decision, reason = final_decision(gate, sg, n_days=602, cfg=CFG)
    assert decision == ADOPT
    assert reason["subgroup_assurance"] == "full"


def test_069_f02_wide_but_conclusive_subgroups_keep_passing():
    """069's ``2026_only`` had a CI half-width of 0.0093 — wider than its margin — yet its upper
    bound was below the margin. A concluded test is a conclusion; v3 must not downgrade it."""
    assert three_way(-0.01634, 0.00224, 0.005) == "PASS"


# --- fix 4: one gate implementation, both paths ------------------------------------------------

def _core(**over):
    args = dict(
        diff=-0.01, ci_low=-0.02, ci_high=-0.001, recent={"pass": True},
        top2_diff=-0.0001, top3_diff=-0.0001, cand_ece=0.0008, act_ece=0.0009, cfg=CFG,
    )
    args.update(over)
    return evaluate_core_gate(**args)


def test_core_gate_conjunction_and_legacy_field_mapping():
    core = _core()
    assert core.adopted is True
    assert core.primary and core.stat_guard and core.recent
    assert core.top_noninferior and core.calibration
    assert set(core.sub_gates) == {
        "effect_beats_delta", "ci_upper_below_zero", "recent_no_evidence_of_harm",
        "top2_noninferior", "top3_noninferior", "calibration_noninferior",
        "calibration_not_emergency",
    }


def test_min_effect_delta_now_applies_to_the_standard_path_too():
    """v2's standard path had no minimum effect: a 1e-9 win with a clean CI passed `primary`."""
    cfg = {**CFG, "min_effect_delta": 0.002}
    assert _core(diff=-0.0001, cfg=cfg).primary is False
    assert _core(diff=-0.003, cfg=cfg).primary is True
    assert _core(diff=-0.0001).primary is True  # absent -> 0.0 -> v2 behaviour preserved


def test_missing_ece_fails_closed_rather_than_defaulting_to_fine():
    core = _core(cand_ece=None)
    assert core.calibration is False
    assert core.reasons["ece_available"] is False


def test_build_gate_delegates_to_the_shared_core():
    """The paired path must not carry its own copy of the conditions (that divergence is what
    left the regime path without a recent-window guard at all)."""
    from horseracing_eval.paired import ArmScores

    def arm(nll, top2, top3, ece):
        return ArmScores(
            winner_nll=nll, winner_excluded=0, started_all={},
            ece_equal_width_like={"ece": ece}, ece_by_band={},
            top2_logloss=top2, top3_logloss=top3,
        )

    g = _build_gate(
        arm(2.05, 0.33, 0.42, 0.0008), arm(2.06, 0.34, 0.43, 0.0009),
        {"ci_low": -0.02, "ci_high": -0.001}, {"pass": True}, CFG,
    )
    assert isinstance(g, GateResult) and g.adopted is True
    assert "sub_gates" in g.reasons and g.reasons["sub_gates"]["recent_no_evidence_of_harm"] is True


def test_reject_still_reachable_on_evidence_of_harm():
    """The corrections must not make REJECT unreachable — only noise-driven REJECTs go away."""
    gate = GateResult(
        primary=False, stat_guard=False, recent_guard=True, top_noninferior=True,
        calibration=True, adopted=False, reasons={"winner_nll_diff": +0.0008},
    )
    states = {"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"}
    sg = {"subgroup_decisions": states, "subgroup_guard": True,
          "subgroup_guard_status": "PASS"}
    assert final_decision(gate, sg, n_days=800, cfg=CFG)[0] == REJECT


# --- disclosure: "no FAIL" must never read as "no harm" (codex) --------------------------------

def test_inconclusive_subgroup_reports_the_degradation_its_interval_still_admits():
    from horseracing_eval.subgroups import residual_risk

    # 088's 2026_only: adoption under NOT_PROVEN would still have allowed +0.0083 winner NLL there
    assert residual_risk(0.008261, LOWP) == 0.008261
    assert residual_risk(0.001108, "NO_DECISION") == 0.001108
    assert residual_risk(-0.00471, "PASS") is None  # concluded -> nothing residual to disclose
    assert residual_risk(None, LOWP) is None


def test_partial_assurance_adoption_states_what_was_not_established():
    gate = GateResult(
        primary=True, stat_guard=True, recent_guard=True, top_noninferior=True,
        calibration=True, adopted=True, reasons={"winner_nll_diff": -0.01},
    )
    sg = {
        "subgroup_decisions": {"2026_only": LOWP, "nk": "PASS", "2026_nk": "PASS"},
        "subgroup_guard": False, "subgroup_guard_status": "NOT_PROVEN",
        "critical_residual_risk": {"2026_only": 0.008261, "nk": None, "2026_nk": None},
    }
    decision, reason = final_decision(gate, sg, n_days=821, cfg=CFG)
    assert decision == ADOPT
    assert "NOT established" in reason["claim"]
    assert reason["critical_residual_risk"]["2026_only"] == 0.008261

    full = {**sg, "subgroup_decisions": {"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"},
            "subgroup_guard": True, "subgroup_guard_status": "PASS"}
    _, reason_full = final_decision(gate, full, n_days=821, cfg=CFG)
    assert "non-inferiority was established in every critical subgroup" in reason_full["claim"]


def test_percentile_interval_precision_uses_the_upper_arm():
    """A skewed interval whose half-width clears the margin but whose UPPER ARM does not is a
    conclusive-precision test; judging on the half-width would misclassify it (codex)."""
    # point -0.0015, arms: lower 0.0025, upper 0.0008 -> half-width 0.00165 >= margin 0.001,
    # but the upper arm (0.0008) is below it, so the test could have concluded.
    assert three_way(-0.004, -0.0007, 0.001, point=-0.0015) == "PASS"
    assert three_way(-0.004, 0.0012, 0.001, point=0.0004) == "NO_DECISION"
