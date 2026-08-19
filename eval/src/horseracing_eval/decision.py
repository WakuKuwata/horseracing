"""Feature 073 US1: single tri-value adoption decision + gate-config hashing / confirmatory guard.

The 068/069 gate returned a boolean ``adopted`` plus a SEPARATE subgroup guard, so the final
call was assembled by hand. 073 folds both — plus eval-window / min-days sufficiency — into one
machine-decided enum (``ADOPT`` / ``REJECT`` / ``NO_DECISION``) so no operator judgement is needed
(FR-001/002, research D2). ``final_decision`` is a pure function of the existing gate booleans and
the subgroup guard, so the legacy ``GateResult`` fields are unchanged (backward compatible).

Mapping:
- NO_DECISION: too few eval days (underpowered), a critical subgroup was never COMPUTED (wiring
  fault), or the main gate is better on the primary point estimate but its CI still straddles 0.
- REJECT: candidate worse on the primary point estimate, or a hard guard (recent / top2-3 /
  calibration) fails with sufficient data, or a critical subgroup is CONFIDENTLY worse (FAIL).
- ADOPT: main gate fully passes AND no critical subgroup is confidently worse.

Contract v3 changes the subgroup arm. v2 required every critical subgroup to PASS, so a subgroup
whose CI was wider than its own margin — a test that could only conclude by proving superiority on
1/12 of the data — produced the same verdict as a genuinely harmful one. Measured over the recorded
history, the guard never once FAILed; its only observed effect was converting an inconclusive run
into "REJECT". v3 vetoes on evidence of harm only, and discloses the assurance level
(``subgroup_assurance``: ``full`` when every critical subgroup PASSed, ``partial`` otherwise) so an
adoption made without full subgroup assurance is visible rather than implied.
"""

from __future__ import annotations

from .hashing import stable_hash

ADOPT = "ADOPT"
REJECT = "REJECT"
NO_DECISION = "NO_DECISION"

#: v3 (2026-08): unified gate implementation, recent-window non-inferiority test, power-aware
#: subgroup states, window-derived target year.
#: v4 (2026-08-18): the interval the gate reads now includes the declared RETRAINING variance.
#: The cluster bootstrap resamples races and is blind to refitting; leaving it out made the
#: interval ~20% too narrow and the effective false-positive rate 5.8% against a nominal 2.5%.
#: A gate-config from an older contract FAILS CLOSED in confirmatory mode on purpose — its numbers
#: were judged under different rules, and silently re-judging them would break the immutability of
#: the verdict it recorded.
EVALUATION_CONTRACT_VERSION = "v4"

#: Feature 091: the only artifact kind a verdict may be read from.
VERDICT_ARTIFACT_KIND = "full_walk_forward"


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

    # 2. critical subgroups. v3: FAIL (confidently worse) => REJECT; MISSING (never computed) =>
    # fail-closed NO_DECISION; NOT_PROVEN (untestable at this margin) => disclosed, not vetoed.
    from .subgroups import (
        GUARD_FAIL,
        GUARD_MISSING,
        GUARD_PASS,
        subgroup_guard_status,
    )

    sg_states = None
    sg_status = None
    if subgroups is not None and critical:
        decisions = subgroups.get("subgroup_decisions", {})
        sg_states = {c: decisions.get(c, GUARD_MISSING) for c in critical}
        sg_status = subgroups.get("subgroup_guard_status") or subgroup_guard_status(
            sg_states, critical
        )
        if sg_status == GUARD_FAIL:
            return REJECT, {"cause": "critical_subgroup_fail", "subgroups": sg_states}
        if sg_status == GUARD_MISSING:
            return NO_DECISION, {
                "cause": "critical_subgroup_not_computed", "subgroups": sg_states,
            }

    # 3. main gate.
    gate_flags = {
        "primary": gate.primary, "stat_guard": gate.stat_guard,
        "recent_guard": gate.recent_guard, "top_noninferior": gate.top_noninferior,
        "calibration": gate.calibration,
    }
    if gate.adopted:
        # codex C#3 fail-closed: when the config DECLARES critical subgroups but the caller did
        # not compute them (subgroups is None, e.g. paired-eval without --subgroups), ADOPT must
        # not be reachable — that would silently skip the pre-registered guard. A hard main-gate
        # failure above still REJECTs normally; only the passing path is intercepted.
        if subgroups is None and critical:
            return NO_DECISION, {
                "cause": "critical_subgroups_not_computed", "critical": list(critical),
                "gate": gate_flags,
                "hint": "re-run with subgroup computation enabled (--subgroups)",
            }
        return ADOPT, {
            "cause": "all_gates_pass",
            "subgroups": sg_states,
            "gate": gate_flags,
            # v3: an adoption reached without full subgroup assurance says so, on the verdict
            # itself — the reader must not have to reconstruct it from the subgroup table.
            "subgroup_assurance": (
                "full" if sg_status in (None, GUARD_PASS) else "partial"
            ),
            "subgroup_guard_status": sg_status,
            "critical_residual_risk": (subgroups or {}).get("critical_residual_risk"),
            # Spelled out so the verdict cannot be read as more than it is (codex): "no FAIL" is
            # not "no harm", and only a full-assurance adoption carries the subgroup claim.
            "claim": (
                "candidate improves the primary metric AND non-inferiority was established in "
                "every critical subgroup"
                if sg_status in (None, GUARD_PASS)
                else "candidate improves the primary metric; in at least one critical subgroup "
                     "non-inferiority was NOT established — no degradation beyond the margin was "
                     "detected, see critical_residual_risk for what the interval still admits"
            ),
        }

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


class VerdictSourceError(RuntimeError):
    """Raised when a verdict is read from an artifact that is not verdict-eligible (Feature 091)."""


def assert_verdict_eligible(report: dict) -> None:
    """Feature 091 (FR-026, codex #4): only a full walk-forward may decide adoption.

    Acceptance runs and diagnostic arms are computed over folds that sit INSIDE the confirmatory
    window. Reading an effect off them and acting on it is a selection leak, so the loader refuses
    them structurally rather than relying on the operator remembering which file is which.
    """
    kind = report.get("artifact_kind")
    if kind != VERDICT_ARTIFACT_KIND:
        raise VerdictSourceError(
            f"artifact_kind={kind!r} cannot decide a verdict (only {VERDICT_ARTIFACT_KIND!r} can). "
            "Acceptance and diagnostic runs share folds with the confirmatory window."
        )
    if not report.get("eligible_for_verdict", False):
        raise VerdictSourceError("artifact is marked eligible_for_verdict=false")


def assert_confirmatory(
    cfg: dict | None, *, expected_hash: str | None, eval_window: dict | None = None
) -> None:
    """Confirmatory-mode fail-closed checks (FR-002): unknown/missing config, window mismatch,
    or gate-config hash mismatch all raise instead of silently proceeding.

    v3: the hash and the window are now REQUIRED, not "checked if supplied". Previously an
    operator who ran ``--confirmatory`` without ``--gate-config-hash`` got a run that skipped the
    hash comparison entirely, and omitting BOTH ``--from`` and ``--to`` skipped the window
    comparison — leaving only "a config exists and says v3". A pre-registration whose enforcement
    depends on the operator remembering three flags is not an enforcement (2026-08 multi-codex
    review).
    """
    if not cfg:
        raise ConfirmatoryContractError("confirmatory mode requires a gate-config (missing)")
    if cfg.get("evaluation_contract_version") != EVALUATION_CONTRACT_VERSION:
        raise ConfirmatoryContractError(
            f"gate-config evaluation_contract_version != {EVALUATION_CONTRACT_VERSION!r}"
        )
    # v4: the retraining-variance term is REQUIRED, not optional. Making it opt-in would rebuild
    # the "fail-open unless the operator remembers" shape that this contract removed three times
    # over in the 2026-08 review.
    sn = (cfg.get("seed_noise") or {})
    if not sn.get("sd_fold"):
        raise ConfirmatoryContractError(
            "confirmatory mode requires a seed_noise.sd_fold (the measured fold-level retraining "
            "SD). The cluster bootstrap cannot see refitting variance; without this the interval "
            "understates and the gate runs above its nominal error rate. Measure it with "
            "scripts/seed_variance_probe.py, or restate the last measurement and cite it."
        )
    if expected_hash is None:
        raise ConfirmatoryContractError(
            "confirmatory mode requires the expected gate-config hash (--gate-config-hash). "
            f"This config hashes to {gate_config_hash(cfg)!r}; pass it to prove the frozen "
            "config is the one being used."
        )
    if gate_config_hash(cfg) != expected_hash:
        raise ConfirmatoryContractError("gate-config hash mismatch (config changed after freeze)")
    if eval_window is None or eval_window.get("from") is None or eval_window.get("to") is None:
        raise ConfirmatoryContractError(
            "confirmatory mode requires BOTH --from and --to so the scored window can be checked "
            "against the pre-registered eval_window (omitting them scored an unverified window)."
        )
    cfg_win = _strip_comments(cfg.get("eval_window", {}) or {})
    want = _strip_comments(eval_window)
    if (cfg_win.get("from"), cfg_win.get("to")) != (want.get("from"), want.get("to")):
        raise ConfirmatoryContractError("eval window mismatch vs pre-registered gate-config")
