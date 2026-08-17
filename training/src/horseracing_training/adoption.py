"""Adoption gate (R6, contracts/adoption.md): compare model vs baseline on identical eval.

active iff:
    win_logloss(model)  <  win_logloss(baseline)        # strictly better on the primary label
    and top2_logloss(model) <= top2_logloss(baseline)   # no regression
    and top3_logloss(model) <= top3_logloss(baseline)
    and win_ece(model)  <= ece_threshold                # pre-fixed threshold (set before metrics)
else candidate.

``model_summary`` / ``baseline_summary`` are ``EvalResult.to_summary()`` -shaped dicts
(``{"eval": {"overall": {label: {metric: value}}}}``) — the same JSONB stored on
``model_versions.metrics_summary`` for baselines (Feature 003 saves it via
``save_baseline``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdoptionGate:
    ece_threshold: float


@dataclass(frozen=True)
class AdoptionDecision:
    adopted: bool  # True -> active, False -> candidate
    reasons: dict


def _overall(summary: dict) -> dict:
    return summary["eval"]["overall"]


def _logloss(overall: dict, label: str) -> float:
    return float(overall[label]["log_loss"])


def evaluate_gate(
    model_summary: dict, baseline_summary: dict, gate: AdoptionGate
) -> AdoptionDecision:
    m = _overall(model_summary)
    b = _overall(baseline_summary)

    win_ll_m, win_ll_b = _logloss(m, "win"), _logloss(b, "win")
    top2_ll_m, top2_ll_b = _logloss(m, "top2"), _logloss(b, "top2")
    top3_ll_m, top3_ll_b = _logloss(m, "top3"), _logloss(b, "top3")
    win_ece_m = float(m["win"]["ece"])

    reasons = {
        "win_logloss_better": {
            "pass": win_ll_m < win_ll_b,
            "model": win_ll_m,
            "baseline": win_ll_b,
        },
        "top2_logloss_no_regression": {
            "pass": top2_ll_m <= top2_ll_b,
            "model": top2_ll_m,
            "baseline": top2_ll_b,
        },
        "top3_logloss_no_regression": {
            "pass": top3_ll_m <= top3_ll_b,
            "model": top3_ll_m,
            "baseline": top3_ll_b,
        },
        "win_ece_within_threshold": {
            "pass": win_ece_m <= gate.ece_threshold,
            "model": win_ece_m,
            "threshold": gate.ece_threshold,
        },
    }
    adopted = all(r["pass"] for r in reasons.values())
    return AdoptionDecision(adopted=adopted, reasons=reasons)


# ---------------------------------------------------------------------------------------------
# Promotion boundary (2026-08 multi-codex review)
#
# ``evaluate_gate`` above is the LEGACY gate: four point-estimate comparisons against a stored
# baseline summary. It has no paired design, no confidence interval, no subgroup guard, no
# confirmatory contract and no artifact isolation — none of which existed when it was written.
# But it was the ONLY thing standing between a freshly trained model and ``adoption_status=active``,
# so a candidate that evaluation contract v3 would REJECT (say, confidently worse in the current
# regime) could still go live by beating those four numbers.
#
# Going ACTIVE now additionally requires a v3 verdict that (a) came from a verdict-eligible
# artifact, (b) says ADOPT, and (c) has FULL subgroup assurance. Partial assurance is exactly the
# case the harness cannot speak about — it belongs in a candidate row awaiting current-regime
# evidence, which is what 085/091 already did by hand.
# ---------------------------------------------------------------------------------------------

CONTRACT_VERSION = "v3"
VERDICT_ARTIFACT_KIND = "full_walk_forward"


@dataclass(frozen=True)
class PromotionDecision:
    """Whether a model may become ACTIVE, and why (recorded on the model row)."""

    promotable: bool
    status: str  # "active" | "candidate"
    reasons: dict


def normalize_verdict(report: dict | None) -> dict | None:
    """Read either report shape into one view.

    The two evaluation paths spell the verdict differently: ``paired.PairedReport`` carries
    ``decision`` / ``decision_reason`` at the top level, while ``regime_paired.RegimeReport``
    nests it under ``verdict`` and adds ``artifact_kind`` / ``eligible_for_verdict``.
    """
    if not report:
        return None
    v = report.get("verdict") or {}
    status = v.get("status") or report.get("decision")
    reason = v.get("decision_reason") or report.get("decision_reason") or {}
    return {
        "status": status,
        "subgroup_assurance": v.get("subgroup_assurance") or reason.get("subgroup_assurance"),
        "contract_version": report.get("evaluation_contract_version")
        or (report.get("gate_config") or {}).get("evaluation_contract_version"),
        # absent on the standard path, which has no acceptance/diagnostic arms to isolate
        "artifact_kind": report.get("artifact_kind"),
        "eligible_for_verdict": report.get("eligible_for_verdict"),
    }


def evaluate_promotion(
    *, legacy: AdoptionDecision, verdict: dict | None, register_as_candidate: bool = False
) -> PromotionDecision:
    """Fold the legacy gate and the v3 verdict into one ACTIVE/CANDIDATE decision.

    Never raises: a run that cannot justify promotion still saves its artifact as a CANDIDATE with
    the reason recorded, because losing a trained model to a contract error helps nobody.
    """
    reasons: dict = {"legacy_gate_adopted": legacy.adopted}
    if register_as_candidate:
        reasons["cause"] = "register_as_candidate_requested"
        return PromotionDecision(False, "candidate", reasons)
    if not legacy.adopted:
        reasons["cause"] = "legacy_gate_not_adopted"
        return PromotionDecision(False, "candidate", reasons)

    v = normalize_verdict(verdict)
    reasons["v3_verdict"] = v
    if v is None:
        reasons["cause"] = "no_v3_verdict_supplied"
        reasons["hint"] = (
            "run `paired-eval --confirmatory --gate-config-hash ... --subgroups` and pass its "
            "report; the legacy 4-metric gate alone cannot justify an ACTIVE promotion"
        )
        return PromotionDecision(False, "candidate", reasons)
    if v["contract_version"] != CONTRACT_VERSION:
        reasons["cause"] = "verdict_contract_version_mismatch"
        return PromotionDecision(False, "candidate", reasons)
    if v["artifact_kind"] is not None and (
        v["artifact_kind"] != VERDICT_ARTIFACT_KIND or not v["eligible_for_verdict"]
    ):
        # acceptance / diagnostic / exploratory arms share folds with the confirmatory window
        reasons["cause"] = "verdict_artifact_not_eligible"
        return PromotionDecision(False, "candidate", reasons)
    if v["status"] != "ADOPT":
        reasons["cause"] = "v3_verdict_not_adopt"
        return PromotionDecision(False, "candidate", reasons)
    if v["subgroup_assurance"] not in (None, "full"):
        # "no FAIL" is not "no harm": an untestable critical subgroup means this run cannot speak
        # about that population, so the model waits as a candidate for current-regime evidence.
        reasons["cause"] = "subgroup_assurance_not_full"
        return PromotionDecision(False, "candidate", reasons)
    reasons["cause"] = "legacy_gate_and_v3_verdict_agree"
    return PromotionDecision(True, "active", reasons)
