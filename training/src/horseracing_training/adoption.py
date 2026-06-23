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
