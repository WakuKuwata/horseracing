"""T017: GateResult conditions — ECE emergency-stop trip + recent-guard AND (analyze T1/C2)."""

from __future__ import annotations

from horseracing_eval.paired import ArmScores, _build_gate

_CFG = {
    "top_noninferior": {"top2": 0.0005, "top3": 0.0005},
    "calibration": {"noninferior_width": 0.001, "emergency_abs_ece": 0.05},
}


def _arm(nll, ece, top2=0.30, top3=0.40):
    return ArmScores(
        winner_nll=nll, winner_excluded=0,
        started_all={"log_loss": 0.2, "brier": 0.1},
        ece_equal_width_like={"ece": ece, "n_bins": 10, "bin_counts": []},
        ece_by_band={}, top2_logloss=top2, top3_logloss=top3,
    )


def _pass_ci():
    return {"ci_high": -0.001}


def _pass_recent():
    return {"pass": True, "windows": {}}


def test_emergency_stop_forces_calibration_fail_even_when_noninferiority_passes():
    # both arms ECE 0.06: diff 0 <= width (non-inferiority passes) BUT abs 0.06 >= 0.05 -> fail.
    cand = _arm(nll=2.0, ece=0.06)
    act = _arm(nll=2.1, ece=0.06)
    g = _build_gate(cand, act, _pass_ci(), _pass_recent(), _CFG)
    assert g.reasons["emergency_stop"] is True
    assert g.calibration is False
    assert g.adopted is False  # emergency stop blocks adoption despite primary win


def test_calibration_passes_below_emergency_and_within_width():
    cand = _arm(nll=2.0, ece=0.02)
    act = _arm(nll=2.1, ece=0.02)
    g = _build_gate(cand, act, _pass_ci(), _pass_recent(), _CFG)
    assert g.reasons["emergency_stop"] is False
    assert g.calibration is True


def test_recent_guard_fail_blocks_adoption():
    cand = _arm(nll=2.0, ece=0.02)
    act = _arm(nll=2.1, ece=0.02)
    g = _build_gate(cand, act, _pass_ci(), {"pass": False, "windows": {}}, _CFG)
    assert g.recent_guard is False
    assert g.adopted is False


def test_top_noninferiority_fail_blocks_adoption():
    cand = _arm(nll=2.0, ece=0.02, top2=0.40)  # much worse top2 than active
    act = _arm(nll=2.1, ece=0.02, top2=0.30)
    g = _build_gate(cand, act, _pass_ci(), _pass_recent(), _CFG)
    assert g.top_noninferior is False
    assert g.adopted is False


def test_full_pass_adopts():
    cand = _arm(nll=2.0, ece=0.02, top2=0.30, top3=0.40)
    act = _arm(nll=2.1, ece=0.02, top2=0.30, top3=0.40)
    g = _build_gate(cand, act, _pass_ci(), _pass_recent(), _CFG)
    assert g.primary and g.stat_guard and g.recent_guard and g.top_noninferior and g.calibration
    assert g.adopted is True
