"""Research retention must never become an implicit production verdict."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "small_gain_research.py"
SPEC = importlib.util.spec_from_file_location("small_gain_research", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
assess_research = module.assess_research


@pytest.fixture
def report():
    return {
        "evaluation_contract_version": "v4",
        "periods": {"all": {"diff": -0.000001}},
        "n_eligible": 100,
        "total_ci": {"point": -0.000001, "ci_low": -0.01, "ci_high": 0.01, "n_days": 20},
        "gate": {
            "primary": False, "stat_guard": False, "adopted": False,
            "recent_guard": True, "top_noninferior": True, "calibration": True,
            "reasons": {"min_effect_delta": 0.003519, "top2_diff": 0.0, "top3_diff": 0.0,
                        "cand_ece": 0.002, "act_ece": 0.002},
        },
        "subgroups": {
            "critical": ["canonical", "recent_year_only"],
            "subgroup_decisions": {"canonical": "PASS", "recent_year_only": "INCONCLUSIVE_LOW_PRECISION"},
            "subgroup_guard_status": "NOT_PROVEN",
            "critical_residual_risk": {"canonical": None, "recent_year_only": 0.02},
        },
        "stage": "screen", "gate_readout": "REJECT", "progression": "ADVANCE",
        "decision_reason": {"cause": "gate_hard_fail"},
    }


def test_tiny_improvement_retained_without_reclassifying_legacy_verdict(report):
    before = deepcopy(report)
    result = assess_research(report)
    assert result["state"] == "RETAIN_UNCERTAIN"
    assert result["original_result"]["gate_readout"] == "REJECT"
    assert result["original_scope"]["stage"] == "screen"
    assert result["artifact_kind"] == "small_gain_research_disposition"
    assert result["eligible_for_verdict"] is False
    assert result["can_adopt"] is False
    assert "decision" not in result
    assert report == before
    result["original_result"]["decision_reason"]["cause"] = "changed"
    assert report == before


def test_supported_is_still_research_only(report):
    report["total_ci"].update(ci_low=-0.000002, ci_high=-0.0000001)
    result = assess_research(report)
    assert result["state"] == "RETAIN_SUPPORTED"
    assert not result["can_adopt"] and not result["eligible_for_verdict"]


@pytest.mark.parametrize("diff", [0.0, 0.000001, 0.005])
def test_nonnegative_point_deferred(report, diff):
    report["periods"]["all"]["diff"] = report["total_ci"]["point"] = diff
    assert assess_research(report)["state"] == "DEFER"


def test_ci_upper_exactly_zero_is_uncertain(report):
    report["total_ci"]["ci_high"] = 0.0
    assert assess_research(report)["state"] == "RETAIN_UNCERTAIN"


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "-0.001", True])
def test_invalid_point_blocks(report, value):
    report["periods"]["all"]["diff"] = value
    assert assess_research(report)["state"] == "BLOCKED"


@pytest.mark.parametrize("field", ["point", "ci_low", "ci_high", "n_days"])
def test_missing_ci_field_blocks(report, field):
    del report["total_ci"][field]
    assert assess_research(report)["state"] == "BLOCKED"


@pytest.mark.parametrize("field", ["top2_diff", "top3_diff", "cand_ece", "act_ece"])
def test_nonfinite_metric_blocks_even_when_gate_says_pass(report, field):
    report["gate"]["reasons"][field] = float("inf")
    assert assess_research(report)["state"] == "BLOCKED"


@pytest.mark.parametrize("field", ["recent_guard", "top_noninferior", "calibration"])
@pytest.mark.parametrize("value", [None, False, 1, "true"])
def test_quality_guard_must_be_explicit_true(report, field, value):
    report["gate"][field] = value
    assert assess_research(report)["state"] == "BLOCKED"


@pytest.mark.parametrize("state", ["FAIL", "MISSING", "UNKNOWN", None, [], {}])
def test_missing_or_failed_critical_subgroup_blocks(report, state):
    report["subgroups"]["subgroup_decisions"]["recent_year_only"] = state
    assert assess_research(report)["state"] == "BLOCKED"


def test_not_proven_is_disclosed_without_veto(report):
    result = assess_research(report)
    assert result["state"] == "RETAIN_UNCERTAIN"
    assert result["subgroup_evidence"]["status"] == "NOT_PROVEN"
    assert result["subgroup_evidence"]["critical_residual_risk"]["recent_year_only"] == 0.02
    assert result["subgroup_evidence"]["assurance"] == "partial_or_unspecified"


@pytest.mark.parametrize("field", ["subgroups", "evaluation_contract_version", "n_eligible"])
def test_missing_evidence_blocks(report, field):
    del report[field]
    assert assess_research(report)["state"] == "BLOCKED"


@pytest.mark.parametrize("value", [0, -1, 1.2, True])
def test_invalid_counts_block(report, value):
    report["n_eligible"] = value
    assert assess_research(report)["state"] == "BLOCKED"


def test_ci_order_and_point_consistency(report):
    report["total_ci"]["ci_low"] = 1.0
    assert assess_research(report)["state"] == "BLOCKED"
    report["total_ci"].update(ci_low=-0.01, point=-0.1)
    assert assess_research(report)["state"] == "BLOCKED"


def test_cli_provenance_and_append_only(report, tmp_path):
    source = tmp_path / "report.json"
    output = tmp_path / "dispositions.json"
    raw = json.dumps(report).encode()
    source.write_bytes(raw)
    module.main(["--report", str(source), "--output", str(output)])
    result = json.loads(output.read_text())
    record = result["records"][0]
    assert record["source_file"] == str(source.resolve())
    assert record["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert record["method_sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    saved = output.read_bytes()
    with pytest.raises(SystemExit):
        module.main(["--report", str(source), "--output", str(output)])
    assert output.read_bytes() == saved
    assert source.read_bytes() == raw


@pytest.mark.parametrize("path", [
    "specs/110-feature-pruning/evidence/full-relative_ability.json",
    "specs/111-ability-observation/evidence/full-observation_age.json",
])
def test_completed_historical_studies_remain_uncertain_candidates(path):
    source = SCRIPT.parent.parent / path
    raw = source.read_bytes()
    result = assess_research(json.loads(raw))
    assert result["state"] == "RETAIN_UNCERTAIN"
    assert result["original_result"]["gate_readout"] == "REJECT"
    assert result["original_scope"]["stage"] == "full"
    assert source.read_bytes() == raw
