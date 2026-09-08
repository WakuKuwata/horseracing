"""Retain small historical improvements without issuing a production verdict.

This opt-in research policy reads, but never rejudges or mutates, v4 evidence.
The old effect-size floor and primary/statistical flags intentionally do not
control retention. Existing quality/harm guards still do. No output from this
module is eligible for model promotion, even when an interval excludes zero.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

POLICY_VERSION = "small_gain_research_v1"
ARTIFACT_KIND = "small_gain_research_disposition"
_SUBGROUP_STATES = {"PASS", "FAIL", "MISSING", "NO_DECISION", "INCONCLUSIVE_LOW_PRECISION"}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _finite(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _positive_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def assess_research(report: dict) -> dict:
    """Return a research disposition; malformed/incomplete evidence fails closed.

    RETAIN_SUPPORTED only describes the supplied historical interval. It is not
    a confirmatory claim, proof of an unused holdout, or permission to activate.
    """
    report = _mapping(report)
    gate = _mapping(report.get("gate"))
    reasons = _mapping(gate.get("reasons"))
    ci = _mapping(report.get("total_ci"))
    diff = _mapping(_mapping(report.get("periods")).get("all")).get("diff")
    groups = _mapping(report.get("subgroups"))
    critical = groups.get("critical")
    states = _mapping(groups.get("subgroup_decisions"))
    errors = []
    if report.get("evaluation_contract_version") != "v4":
        errors.append("unsupported_or_missing_source_contract")
    if not _finite(diff):
        errors.append("missing_or_nonfinite_primary_diff")
    if not _positive_count(report.get("n_eligible")):
        errors.append("missing_or_invalid_n_eligible")
    if not all(_finite(ci.get(k)) for k in ("point", "ci_low", "ci_high")):
        errors.append("missing_or_nonfinite_total_ci")
    elif ci["ci_low"] > ci["ci_high"]:
        errors.append("invalid_total_ci_order")
    elif _finite(diff) and not math.isclose(ci["point"], diff, rel_tol=1e-9, abs_tol=1e-12):
        errors.append("total_ci_point_mismatch")
    if not _positive_count(ci.get("n_days")):
        errors.append("missing_or_invalid_total_ci_days")
    for key in ("top2_diff", "top3_diff", "cand_ece", "act_ece"):
        if not _finite(reasons.get(key)):
            errors.append(f"missing_or_nonfinite_{key}")
    for key in ("cand_ece", "act_ece"):
        if _finite(reasons.get(key)) and not 0 <= reasons[key] <= 1:
            errors.append(f"invalid_{key}_range")
    for key in ("recent_guard", "top_noninferior", "calibration"):
        if gate.get(key) is not True:
            errors.append(f"quality_guard_not_passed:{key}")

    # Requiring an explicit declaration prevents an absent subgroup payload from
    # silently meaning "none required". An explicitly empty list is permitted.
    if not isinstance(critical, list) or any(not isinstance(c, str) or not c for c in critical):
        errors.append("critical_subgroup_declaration_missing_or_invalid")
        critical = []
    elif len(critical) != len(set(critical)):
        errors.append("duplicate_critical_subgroups")
    for name in critical:
        state = states.get(name)
        if not isinstance(state, str) or state not in _SUBGROUP_STATES:
            errors.append(f"critical_subgroup_uncomputed_or_unknown:{name}")
        elif state in {"FAIL", "MISSING"}:
            errors.append(f"critical_subgroup_{state.lower()}:{name}")
    status = groups.get("subgroup_guard_status")
    if critical and (not isinstance(status, str) or status not in {"PASS", "NOT_PROVEN", "FAIL", "MISSING"}):
        errors.append("subgroup_guard_status_missing_or_unknown")
    if isinstance(status, str) and status in {"FAIL", "MISSING"}:
        errors.append(f"subgroup_guard_{status.lower()}")

    if errors:
        state, reason = "BLOCKED", "incomplete_invalid_or_failed_quality_evidence"
    elif diff >= 0:
        state, reason = "DEFER", "no_observed_primary_improvement_in_this_scope"
    elif ci["ci_high"] >= 0:
        state, reason = "RETAIN_UNCERTAIN", "observed_improvement_interval_includes_zero"
    else:
        state, reason = "RETAIN_SUPPORTED", "observed_improvement_historical_interval_below_zero"
    return {
        "artifact_kind": ARTIFACT_KIND,
        "policy_version": POLICY_VERSION,
        "eligible_for_verdict": False,
        "can_adopt": False,
        "state": state,
        "reason": reason,
        "blocking_reasons": errors,
        "primary_diff": diff if _finite(diff) else None,
        "total_ci": deepcopy(ci) if all(_finite(ci.get(k)) for k in ("point", "ci_low", "ci_high")) else None,
        "quality_metrics": {k: reasons.get(k) if _finite(reasons.get(k)) else None
                            for k in ("top2_diff", "top3_diff", "cand_ece", "act_ece")},
        "subgroup_evidence": {
            "critical": list(critical),
            "states": {c: states.get(c) for c in critical},
            "status": status,
            "critical_residual_risk": deepcopy(groups.get("critical_residual_risk")),
            "assurance": "full" if critical and all(states.get(c) == "PASS" for c in critical) else "partial_or_unspecified",
        },
        "original_result": {
            "evaluation_contract_version": report.get("evaluation_contract_version"),
            "gate_readout": report.get("gate_readout"),
            "decision": report.get("decision"),
            "decision_reason": deepcopy(report.get("decision_reason")),
            "progression": report.get("progression"),
        },
        "original_scope": {
            "stage": report.get("stage"),
            "candidate": deepcopy(report.get("candidate")),
            "n_eligible": report.get("n_eligible"),
            "n_days": ci.get("n_days"),
            "target_year": report.get("target_year"),
            "evidence_regime": deepcopy(report.get("evidence_regime")),
            "gate_config_hash": report.get("gate_config_hash"),
            "study_config_hash": report.get("study_config_hash"),
        },
        "limitations": [
            "Research retention only; original verdict is unchanged.",
            "Neither retention state establishes fresh holdout independence or production readiness.",
            "A non-vetoed uncertain subgroup does not establish its non-inferiority; read residual risk.",
            "Individual gains cannot be added; evaluate the cumulative model against its incumbent and the fixed active anchor.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"refusing to overwrite {args.output}")
    method_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    records = []
    for path in args.report:
        raw = path.read_bytes()
        result = assess_research(json.loads(raw))
        result["source_file"] = str(path.resolve())
        result["source_sha256"] = hashlib.sha256(raw).hexdigest()
        result["method_sha256"] = method_sha
        records.append(result)
    payload = {
        "artifact_kind": "small_gain_research_dispositions",
        "policy_version": POLICY_VERSION,
        "eligible_for_verdict": False,
        "can_adopt": False,
        "method_sha256": method_sha,
        "records": records,
    }
    # Serialize before creating the destination; invalid numbers cannot leave a
    # partly written report. Exclusive creation also closes the overwrite race.
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with args.output.open("x") as output:
        output.write(encoded)


if __name__ == "__main__":
    main()
