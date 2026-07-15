"""Feature 073 US1 (T005/T006): single tri-value adoption decision + confirmatory guard.

Pure-function tests over ``final_decision`` / ``gate_config_hash`` / ``assert_confirmatory`` — no
DB, no model fit. Covers the ADOPT/REJECT/NO_DECISION truth table, the eval-window / min-days
boundary, critical-subgroup underpowering, and fail-closed confirmatory checks.
"""

from __future__ import annotations

import pytest

from horseracing_eval.decision import (
    ADOPT,
    EVALUATION_CONTRACT_VERSION,
    NO_DECISION,
    REJECT,
    ConfirmatoryContractError,
    assert_confirmatory,
    assert_verdict_immutable,
    final_decision,
    gate_config_hash,
)
from horseracing_eval.paired import GateResult

CFG = {
    "evaluation_contract_version": "v2",
    "eval_window": {"from": "2019-01-01", "to": "2026-07-12", "min_eval_days": 10},
    "subgroup_guard": {
        "critical_subgroups": ["2026_only", "nk", "2026_nk"],
        "no_decision_min_days": 10,
    },
}


def _gate(*, primary, stat, recent, top, cal, diff):
    adopted = primary and stat and recent and top and cal
    return GateResult(
        primary=primary, stat_guard=stat, recent_guard=recent, top_noninferior=top,
        calibration=cal, adopted=adopted, reasons={"winner_nll_diff": diff},
    )


def _sg(states):
    return {"subgroup_decisions": states, "subgroup_guard": all(v == "PASS" for v in states.values())}


PASS_SG = {"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"}


# --- T005: tri-value truth table ----------------------------------------------------------

def test_adopt_when_main_gate_and_all_critical_subgroups_pass():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg(PASS_SG), n_days=30, cfg=CFG)
    assert d == ADOPT
    assert r["cause"] == "all_gates_pass"


def test_reject_when_primary_point_estimate_worse():
    gate = _gate(primary=False, stat=False, recent=True, top=True, cal=True, diff=+0.02)
    d, r = final_decision(gate, _sg(PASS_SG), n_days=30, cfg=CFG)
    assert d == REJECT
    assert r["cause"] == "gate_hard_fail"


def test_reject_when_critical_subgroup_fails():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg({**PASS_SG, "nk": "FAIL"}), n_days=30, cfg=CFG)
    assert d == REJECT
    assert r["cause"] == "critical_subgroup_fail"


def test_reject_when_calibration_hard_fails():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=False, diff=-0.01)
    d, r = final_decision(gate, _sg(PASS_SG), n_days=30, cfg=CFG)
    assert d == REJECT


def test_no_decision_when_stat_guard_underpowered_but_point_estimate_better():
    # everything good except the CI still straddles 0 -> underpowered, not a rejection.
    gate = _gate(primary=True, stat=False, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg(PASS_SG), n_days=30, cfg=CFG)
    assert d == NO_DECISION
    assert r["cause"] == "stat_guard_underpowered"


def test_adopt_without_subgroups_when_main_gate_passes():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, _ = final_decision(gate, None, n_days=30, cfg=CFG)
    assert d == ADOPT


# --- T006: boundaries ---------------------------------------------------------------------

def test_no_decision_when_below_min_eval_days():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg(PASS_SG), n_days=9, cfg=CFG)  # 9 < 10
    assert d == NO_DECISION
    assert r["cause"] == "insufficient_eval_days"


def test_adopt_exactly_at_min_eval_days():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, _ = final_decision(gate, _sg(PASS_SG), n_days=10, cfg=CFG)  # 10 == min, sufficient
    assert d == ADOPT


def test_no_decision_when_empty_window():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg(PASS_SG), n_days=None, cfg=CFG)
    assert d == NO_DECISION
    assert r["cause"] == "insufficient_eval_days"


def test_no_decision_when_critical_subgroup_underpowered():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg({**PASS_SG, "2026_nk": "NO_DECISION"}), n_days=30, cfg=CFG)
    assert d == NO_DECISION
    assert r["cause"] == "critical_subgroup_underpowered"


def test_no_decision_when_critical_subgroup_missing():
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    d, r = final_decision(gate, _sg({"2026_only": "PASS", "nk": "PASS"}), n_days=30, cfg=CFG)
    assert d == NO_DECISION  # 2026_nk absent -> MISSING


# --- confirmatory guard + hash ------------------------------------------------------------

def test_gate_config_hash_ignores_comment_keys():
    a = dict(CFG)
    b = {**CFG, "_comment": "annotations do not change the hash"}
    assert gate_config_hash(a) == gate_config_hash(b)


def test_gate_config_hash_changes_on_substantive_edit():
    a = dict(CFG)
    b = {**CFG, "subgroup_guard": {**CFG["subgroup_guard"], "no_decision_min_days": 20}}
    assert gate_config_hash(a) != gate_config_hash(b)


def test_confirmatory_fails_closed_on_missing_config():
    with pytest.raises(ConfirmatoryContractError):
        assert_confirmatory(None, expected_hash=None)


def test_confirmatory_fails_closed_on_hash_mismatch():
    with pytest.raises(ConfirmatoryContractError):
        assert_confirmatory(CFG, expected_hash="deadbeef")


def test_confirmatory_passes_on_matching_hash_and_window():
    h = gate_config_hash(CFG)
    assert_confirmatory(CFG, expected_hash=h, eval_window={"from": "2019-01-01", "to": "2026-07-12"})


def test_confirmatory_fails_on_window_mismatch():
    h = gate_config_hash(CFG)
    with pytest.raises(ConfirmatoryContractError):
        assert_confirmatory(CFG, expected_hash=h, eval_window={"from": "2020-01-01", "to": "2026-07-12"})


# --- T024: verdict immutability (FR-015) --------------------------------------------------

def test_new_contract_version_is_v2():
    assert EVALUATION_CONTRACT_VERSION == "v2"


def test_verdict_immutable_allows_fresh_and_rejects_overwrite():
    assert_verdict_immutable(None)  # no prior verdict -> fresh write allowed
    for prior in ("v1", "v2"):
        with pytest.raises(ConfirmatoryContractError):
            assert_verdict_immutable(prior)  # any prior verdict is immutable


# --- T009: leak guard (behavioral) — derived verdicts never mutate inputs (II) -------------

def test_final_decision_is_pure_and_does_not_mutate_inputs():
    # Feature 073 II: the decision is a read-only function of gate + subgroups; it must not mutate
    # them (a mutated gate/subgroup could leak an eval-derived value back into a re-used object).
    gate = _gate(primary=True, stat=True, recent=True, top=True, cal=True, diff=-0.01)
    sg = _sg(dict(PASS_SG))
    gate_before = dict(gate.__dict__)
    sg_before = {"decisions": dict(sg["subgroup_decisions"]), "guard": sg["subgroup_guard"]}
    final_decision(gate, sg, n_days=30, cfg=CFG)
    assert gate.__dict__ == gate_before
    assert sg["subgroup_decisions"] == sg_before["decisions"]
    assert sg["subgroup_guard"] == sg_before["guard"]

