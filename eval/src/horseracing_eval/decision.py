"""Feature 073 US1: single tri-value adoption decision + gate-config hashing / confirmatory guard.

The 068/069 gate returned a boolean ``adopted`` plus a SEPARATE subgroup guard, so the final
call was assembled by hand. 073 folds both — plus eval-window / min-days sufficiency — into one
machine-decided enum (``ADOPT`` / ``REJECT`` / ``NO_DECISION``) so no operator judgement is needed
(FR-001/002, research D2). ``final_decision`` is a pure function of the existing gate booleans and
the subgroup guard, so the legacy ``GateResult`` fields are unchanged (backward compatible).

Mapping:
- NO_DECISION: too few eval days (underpowered), or a critical subgroup is NO_DECISION/MISSING,
  or the main gate is better on the primary point estimate but its CI still straddles 0.
- REJECT: candidate worse on the primary point estimate, or a hard guard (recent / top2-3 /
  calibration) fails with sufficient data, or a critical subgroup FAILs.
- ADOPT: main gate fully passes AND every critical subgroup PASSes (intersection-union).
"""

from __future__ import annotations

from .hashing import stable_hash

ADOPT = "ADOPT"
REJECT = "REJECT"
NO_DECISION = "NO_DECISION"

EVALUATION_CONTRACT_VERSION = "v2"


class ConfirmatoryContractError(RuntimeError):
    """Confirmatory-mode fail-closed: unknown/missing config, window mismatch, or hash mismatch."""


def _strip_comments(obj):
    """Drop ``_``-prefixed annotation keys so the hash is stable across comment edits."""
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def gate_config_hash(cfg: dict) -> str:
    """Canonical content hash of a gate-config, ignoring ``_comment``/``_``-prefixed keys."""
    return stable_hash(_strip_comments(cfg or {}))


def _min_eval_days(cfg: dict) -> int:
    win = (cfg or {}).get("eval_window", {}) or {}
    if "min_eval_days" in win:
        return int(win["min_eval_days"])
    sg = (cfg or {}).get("subgroup_guard", {}) or {}
    return int(sg.get("no_decision_min_days", 0))


def critical_subgroups(cfg: dict) -> list[str]:
    sg = (cfg or {}).get("subgroup_guard", {}) or {}
    return list(sg.get("critical_subgroups", []))


def final_decision(
    gate,
    subgroups: dict | None,
    *,
    n_days: int | None,
    cfg: dict,
) -> tuple[str, dict]:
    """Collapse the main gate + subgroup guard + sufficiency into one tri-value decision.

    ``gate`` is a ``paired.GateResult``; ``subgroups`` is the ``_compute_subgroups`` dict (or
    None when subgroups were not requested). Returns ``(decision, reason)``.
    """
    min_days = _min_eval_days(cfg)
    critical = critical_subgroups(cfg)

    # 1. sufficiency — an empty or too-short window can never silently pass (FR-002).
    if n_days is None or n_days < min_days:
        return NO_DECISION, {
            "cause": "insufficient_eval_days", "n_days": n_days, "min_eval_days": min_days,
        }

    # 2. critical subgroups (intersection-union). FAIL => REJECT; NO_DECISION/MISSING => defer.
    sg_states = None
    if subgroups is not None and critical:
        decisions = subgroups.get("subgroup_decisions", {})
        sg_states = {c: decisions.get(c, "MISSING") for c in critical}
        if any(v == "FAIL" for v in sg_states.values()):
            return REJECT, {"cause": "critical_subgroup_fail", "subgroups": sg_states}
        if any(v in ("NO_DECISION", "MISSING") for v in sg_states.values()):
            return NO_DECISION, {"cause": "critical_subgroup_underpowered", "subgroups": sg_states}

    # 3. main gate.
    gate_flags = {
        "primary": gate.primary, "stat_guard": gate.stat_guard,
        "recent_guard": gate.recent_guard, "top_noninferior": gate.top_noninferior,
        "calibration": gate.calibration,
    }
    if gate.adopted:
        return ADOPT, {"cause": "all_gates_pass", "subgroups": sg_states, "gate": gate_flags}

    # Hard degradations => REJECT (candidate worse on primary, or a confident guard breach).
    if (
        not gate.primary
        or not gate.recent_guard
        or not gate.top_noninferior
        or not gate.calibration
    ):
        return REJECT, {"cause": "gate_hard_fail", "gate": gate_flags}

    # Only the statistical guard is unmet (point estimate better, CI straddles 0) => underpowered.
    return NO_DECISION, {"cause": "stat_guard_underpowered", "gate": gate_flags}


def assert_verdict_immutable(prior_contract_version: str | None) -> None:
    """FR-015 (US3, T027): a previously recorded verdict is immutable. 068/069/070 verdicts are
    ``evaluation_contract_version=v1``; a v2 recomputation is reference-only and must NOT overwrite
    or re-classify any prior verdict. Refuses (fail-closed) if a prior verdict already exists."""
    if prior_contract_version is not None:
        raise ConfirmatoryContractError(
            f"refusing to overwrite an existing verdict (contract_version="
            f"{prior_contract_version!r}); v2 recompute is reference-only (FR-015)."
        )


def assert_confirmatory(
    cfg: dict | None, *, expected_hash: str | None, eval_window: dict | None = None
) -> None:
    """Confirmatory-mode fail-closed checks (FR-002): unknown/missing config, window mismatch,
    or gate-config hash mismatch all raise instead of silently proceeding."""
    if not cfg:
        raise ConfirmatoryContractError("confirmatory mode requires a gate-config (missing)")
    if cfg.get("evaluation_contract_version") != EVALUATION_CONTRACT_VERSION:
        raise ConfirmatoryContractError(
            f"gate-config evaluation_contract_version != {EVALUATION_CONTRACT_VERSION!r}"
        )
    if expected_hash is not None and gate_config_hash(cfg) != expected_hash:
        raise ConfirmatoryContractError("gate-config hash mismatch (config changed after freeze)")
    if eval_window is not None:
        cfg_win = _strip_comments(cfg.get("eval_window", {}) or {})
        want = _strip_comments(eval_window)
        if (cfg_win.get("from"), cfg_win.get("to")) != (want.get("from"), want.get("to")):
            raise ConfirmatoryContractError("eval window mismatch vs pre-registered gate-config")
