"""Feature 091: paired evaluation under TWO input regimes, with a single materialised verdict.

Why this exists: the standard paired-eval scores settled races, where the same-day weight is
present. Under that condition `prev_weight` has nothing to add, so a feature whose entire purpose
is to fix the *serving* input looks worthless. The measurement has to be taken under the condition
predictions are actually made.

  serving regime  -> both arms predict with the same-day weight columns masked  (PRIMARY)
  full-info       -> both arms predict untouched      (non-inferiority guard)

Both arms are masked. The active model has no `prev_weight`, so it simply degrades — that IS the
quantity being measured (what production currently loses at serve time). Masking one arm only
would compare two different questions.

This module COMPOSES `paired.py` rather than editing it, so the existing contract (and the tests
pinning it) stay untouched.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
from typing import Any

from .bootstrap import (
    race_day_cluster_bootstrap_ci_v1,
    race_day_cluster_bootstrap_sensitivity_v2,
)
from .decision import ADOPT, REJECT, final_decision
from .foldfit import predict_over_folds_multi
from .gates import evaluate_core_gate, recent_window_guard
from .hashing import race_set_hash
from .paired import (
    DEFAULT_BAND_EDGES,
    PairedContractError,
    _clip_nll,
    _compute_subgroups,
    _score_arm,
    _winner_probs,
    resolve_target_year,
)
from .splits import assert_scored_window
from .subgroups import GUARD_PASS

SERVING = "serving"
FULL_INFO = "full_info"

#: Only this kind may feed a verdict. Acceptance runs and diagnostic arms are stamped otherwise so
#: the loader can refuse them mechanically (their folds sit inside the confirmatory window, so
#: reading an effect off them would be a selection leak).
VERDICT_KIND = "full_walk_forward"


@dataclass
class RegimeScores:
    """One regime's paired comparison."""

    regime: str
    candidate: dict
    active: dict
    diff: float
    ci_low: float | None
    ci_high: float | None
    n_races: int
    n_days: int
    mask_races_candidate: int
    mask_races_active: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RegimeReport:
    artifact_kind: str
    eligible_for_verdict: bool
    race_set_hash: str
    serving_regime: dict
    full_info_regime: dict
    full_info_guard: bool
    verdict: dict
    #: DIAGNOSTIC ONLY (never gates): winner NLL on the pre-calibration race-softmax. Separates
    #: "the feature helped" from "the calibrator reshuffled things" (codex).
    uncalibrated: dict = field(default_factory=dict)
    #: 069 subgroup CIs + intersection-union guard, computed under the SERVING regime.
    subgroups: dict = field(default_factory=dict)
    gate_config: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    #: DIAGNOSTIC ONLY (073 FR-014): the primary CI re-bucketed into coarser blocks. Never ANDed
    #: into the gate — picking the width that clears zero is the post-hoc estimator choice the
    #: pre-registration exists to prevent.
    bootstrap_sensitivity: dict = field(default_factory=dict)
    #: Per-day paired winner-NLL differences, kept so any later CI question is answerable from the
    #: artifact rather than from another multi-hour re-fit.
    diffs_by_day: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)



def _uncalibrated_diagnostic(valid_races, cand_raw, act_raw, boot) -> dict:
    """Winner NLL on the pre-calibration scores, per regime. Diagnostic — never gates.

    The isotonic map is fitted on one score distribution; masking shifts that distribution. If the
    calibrated and uncalibrated comparisons disagree in SIGN, the calibrator is doing the work and
    the adoption claim needs re-reading.
    """
    out: dict = {}
    for regime in (SERVING, FULL_INFO):
        by_day: dict[str, list[float]] = {}
        cand_nll: list[float] = []
        act_nll: list[float] = []
        for er in valid_races:
            rid = er.context.race_id
            winners = [sl.horse_id for sl in er.labels if int(getattr(sl, "win", 0)) == 1]
            if len(winners) != 1:
                continue
            w = winners[0]
            c = (cand_raw.get(regime) or {}).get(rid, {}).get(w)
            a = (act_raw.get(regime) or {}).get(rid, {}).get(w)
            if c is None or a is None:
                continue
            cn, an = _clip_nll(c), _clip_nll(a)
            cand_nll.append(cn)
            act_nll.append(an)
            by_day.setdefault(er.context.race_date.isoformat(), []).append(cn - an)
        if not cand_nll:
            out[regime] = {"available": False}
            continue
        ci = race_day_cluster_bootstrap_ci_v1(
            by_day, b=boot.get("b", 2000), seed=boot.get("seed", 20260810),
            alpha=float(boot.get("alpha", 0.05)),
        )
        out[regime] = {
            "available": True,
            "candidate_winner_nll": sum(cand_nll) / len(cand_nll),
            "active_winner_nll": sum(act_nll) / len(act_nll),
            "diff": ci.point,
            "ci_low": ci.ci_low,
            "ci_high": ci.ci_high,
            "n_races": len(cand_nll),
        }
    srv, fi = out.get(SERVING, {}), out.get(FULL_INFO, {})
    if srv.get("available"):
        out["note"] = (
            "DIAGNOSTIC ONLY. Compare the sign with serving_regime.diff: agreement means the "
            "feature moved the model; disagreement means the calibrator did."
        )
        out["sign_matches_calibrated"] = None  # filled by the caller-facing report consumer
    _ = fi
    return out


def _paired_diff(valid_races, cand_preds, act_preds) -> tuple[dict, int]:
    """Per-race paired winner-NLL diff (candidate − active), grouped by race day."""
    diffs_by_day: dict[str, list[float]] = {}
    n = 0
    cand_wp = _winner_probs(valid_races, cand_preds, arm="")
    act_wp = _winner_probs(valid_races, act_preds, arm="")
    for er, cp, ap in zip(valid_races, cand_wp, act_wp, strict=True):
        if cp is None or ap is None:
            continue
        n += 1
        diffs_by_day.setdefault(er.context.race_date.isoformat(), []).append(
            _clip_nll(cp) - _clip_nll(ap)
        )
    return diffs_by_day, n


def _count_masked(preds_by_race: dict, spec: Any) -> int:
    """How many races the regime actually touched. 0 when the regime is the default."""
    if spec is None:
        return 0
    return len(preds_by_race)


def evaluate_regimes(
    candidate,
    active,
    eval_races,
    *,
    serving_spec: Any,
    gate_config: dict,
    first_valid_year: int | None = None,
    valid_from: datetime.date | None = None,
    num_threads: int | None = None,
    band_edges: tuple[float, ...] = DEFAULT_BAND_EDGES,
    artifact_kind: str = VERDICT_KIND,
) -> RegimeReport:
    """Score both arms under both regimes and materialise the pre-registered verdict.

    ``serving_spec`` is the opaque predict-regime value for the serving condition (built by the
    caller from the frozen gate-config, never inspected here).
    """
    if serving_spec is None:
        raise PairedContractError(
            "serving_spec must not be None: the PRIMARY regime would silently collapse into "
            "full-info and the comparison would measure nothing (fail-closed)"
        )

    # ``valid_from`` narrows the SCORED side to a day-exact window. Folds are year-granular, so
    # without it a window frozen mid-year silently scores the whole calendar year — for the arm E
    # prospective holdout (frozen 2026-07-13) that is 1,866 already-used development races against
    # 432 genuinely prospective ones, and ``min_eval_days`` is satisfied by the development days
    # alone. ``paired_eval`` has taken this since the 2026-08 review; this path had not.
    kwargs = {"num_threads": num_threads, "valid_from": valid_from}
    if first_valid_year is not None:
        kwargs["first_valid_year"] = first_valid_year
    regimes = {SERVING: serving_spec, FULL_INFO: None}

    cand_by_regime, cand_valid, cand_raw = predict_over_folds_multi(
        candidate, eval_races, regimes=regimes, collect_raw=True, **kwargs
    )
    act_by_regime, act_valid, act_raw = predict_over_folds_multi(
        active, eval_races, regimes=regimes, collect_raw=True, **kwargs
    )

    # Structural check on what was ACTUALLY scored, not on what the caller declared.
    assert_scored_window(cand_valid, valid_from=valid_from)

    cand_ids = {er.context.race_id for er in cand_valid}
    act_ids = {er.context.race_id for er in act_valid}
    if cand_ids != act_ids:
        raise PairedContractError("candidate/active valid race sets differ (fail-closed, C8)")
    # order + winner labels are shared by construction (same eval_races object), but the race set
    # is what the paired contract pins, so hash it.
    rs_hash = race_set_hash(cand_ids)

    boot = gate_config.get("bootstrap", {})
    delta = float(gate_config.get("min_effect_delta", 0.0))
    ni_width = float(gate_config.get("full_info_guard", {}).get("noninferior_width", 0.0))

    scored: dict[str, RegimeScores] = {}
    day_diffs: dict[str, dict[str, list[float]]] = {}
    for name in (SERVING, FULL_INFO):
        spec = regimes[name]
        cand_preds, act_preds = cand_by_regime[name], act_by_regime[name]
        diffs_by_day, n_races = _paired_diff(cand_valid, cand_preds, act_preds)
        # Keep the per-day paired differences. They are the input to every CI variant, so storing
        # them makes any later re-bucketing question (block width, sensitivity, a different alpha)
        # answerable from the artifact instead of costing another multi-hour re-fit.
        day_diffs[name] = diffs_by_day
        ci = race_day_cluster_bootstrap_ci_v1(
            diffs_by_day,
            b=boot.get("b", 2000),
            seed=boot.get("seed", 20260810),
            alpha=float(boot.get("alpha", 0.05)),
        )
        scored[name] = RegimeScores(
            regime=name,
            candidate=_score_arm(cand_valid, cand_preds, band_edges=band_edges).__dict__,
            active=_score_arm(cand_valid, act_preds, band_edges=band_edges).__dict__,
            diff=ci.point,
            ci_low=ci.ci_low,
            ci_high=ci.ci_high,
            n_races=n_races,
            n_days=ci.n_days,
            mask_races_candidate=_count_masked(cand_preds, spec),
            mask_races_active=_count_masked(act_preds, spec),
        )

    uncalibrated = _uncalibrated_diagnostic(cand_valid, cand_raw, act_raw, boot)

    srv = scored[SERVING]
    # INV: the regime must have reached BOTH arms. A one-sided application still produces numbers,
    # so nothing else in the pipeline would notice.
    if srv.mask_races_candidate != srv.mask_races_active:
        raise PairedContractError(
            f"serving regime applied to {srv.mask_races_candidate} candidate races but "
            f"{srv.mask_races_active} active races — the comparison is not paired (fail-closed)"
        )

    # ...but equal COUNTS only prove the regime was OFFERED to both arms. A predictor that accepts
    # the spec and ignores it keeps its counts and still reports a full-info comparison labelled
    # "serving". The observable consequence is that the arm's serving scores are bit-identical to
    # its full-info scores, so check that instead of trusting the handshake.
    fi = scored[FULL_INFO]
    inert = [
        name
        for name, s_arm, f_arm in (
            ("candidate", srv.candidate, fi.candidate),
            ("active", srv.active, fi.active),
        )
        if s_arm["winner_nll"] == f_arm["winner_nll"]
    ]
    # One expected exception, and only for artifacts that can never decide anything: an arm trained
    # with EVERY race masked (the m=1.0 control) genuinely reads no same-day weight, so the serving
    # mask is correctly a no-op on it. That is the property under examination, not a wiring fault —
    # and it is distinguishable from a fault, because a broken mask would leave BOTH arms inert.
    # The strict rule still applies in full to verdict-eligible runs.
    regime_invariance_expected = (
        artifact_kind != VERDICT_KIND and len(inert) == 1 and inert[0] == "candidate"
    )
    if inert and not regime_invariance_expected:
        raise PairedContractError(
            f"serving regime had NO effect on {inert}: winner NLL is bit-identical to full-info. "
            "Either the predictor swallowed set_predict_weight_mask, or that arm reads none of "
            f"{list(gate_config.get('weight_mask', {}).get('columns', []))}. Both make the "
            "'serving regime' label false, so this fails closed rather than reporting a number."
        )

    # --- the full pre-registered gate, not just the headline effect ---------------------------
    # `serving_regime.gate.adopted` in the contract is the 068 gate AND the 091 minimum effect.
    # Evaluating only the effect would let through a candidate that wins on winner NLL while
    # degrading top2/top3 or calibration.
    c_s, a_s = srv.candidate, srv.active

    def _ece(scores):
        return (scores.get("ece_equal_width_like") or {}).get("ece")

    # Contract v3: ONE gate implementation shared with the standard paired path. Before v3 this
    # block was a hand-rolled copy that happened to omit the recent-window guard entirely, so the
    # two "adoption gates" in the repo applied different conditions to the same question.
    # Same reference date as the standard path: the latest VALID race date, not the latest day
    # that happens to carry a paired difference. They diverge when the final day has no
    # single-winner race, which silently shifts this path's window into the past (2026-08 review).
    recent = recent_window_guard(
        day_diffs[SERVING], cfg=gate_config,
        max_date=max(er.context.race_date for er in cand_valid),
    )
    core = evaluate_core_gate(
        diff=srv.diff, ci_low=srv.ci_low, ci_high=srv.ci_high, recent=recent,
        top2_diff=c_s["top2_logloss"] - a_s["top2_logloss"],
        top3_diff=c_s["top3_logloss"] - a_s["top3_logloss"],
        cand_ece=_ece(c_s), act_ece=_ece(a_s), cfg=gate_config,
    )
    sub_gates = core.sub_gates
    primary = core.adopted
    full_info_guard = scored[FULL_INFO].diff <= ni_width

    # --- subgroup guard, computed UNDER THE SERVING REGIME (the verdict path is regime-qualified)
    sg_cfg = gate_config.get("subgroup_guard") or {}
    subgroups: dict | None = None
    if sg_cfg.get("critical_subgroups"):
        srv_cand, srv_act = cand_by_regime[SERVING], act_by_regime[SERVING]
        subgroups = _compute_subgroups(
            cand_valid, srv_cand, srv_act,
            _winner_probs(cand_valid, srv_cand, arm=""),
            _winner_probs(cand_valid, srv_act, arm=""),
            gate_config,
            b=boot.get("b", 2000), seed=boot.get("seed", 20260810),
            alpha=float(boot.get("alpha", 0.05)),
        )
    sg_guard = (subgroups or {}).get("subgroup_guard")
    sg_status = (subgroups or {}).get("subgroup_guard_status")

    # ONE verdict mapping for both evaluation paths (2026-08 multi-codex review, found
    # independently by two lenses). The v3 unification made the two paths agree on the GATE
    # CONDITIONS but left them with different VERDICT mappings: this path collapsed everything
    # non-ADOPT to REJECT and never checked min_eval_days, so an underpowered run — the very case
    # v3 exists to stop mislabelling — came out as "the candidate is worse". `final_decision`
    # already distinguishes REJECT (evidence against) from NO_DECISION (cannot tell), so this path
    # calls it instead of re-deciding. A CoreGate duck-types as a GateResult for that call.
    status, decision_reason = final_decision(core, subgroups, n_days=srv.n_days, cfg=gate_config)
    # full_info non-inferiority is this path's extra term and is not part of the shared mapping:
    # breaching it is evidence of harm under full information, so it downgrades ADOPT to REJECT.
    if status == ADOPT and not full_info_guard:
        status = REJECT
        decision_reason = {"cause": "full_info_guard_breach",
                           "full_info_diff": scored[FULL_INFO].diff, "noninferior_width": ni_width}
    adopt = status == ADOPT
    subgroup_assurance = (
        "full" if sg_status in (None, GUARD_PASS) else "partial"
    )

    return RegimeReport(
        artifact_kind=artifact_kind,
        eligible_for_verdict=(artifact_kind == VERDICT_KIND),
        race_set_hash=rs_hash,
        serving_regime={**scored[SERVING].to_dict(),
                        # the contract quotes `serving_regime.gate.adopted`; make that path
                        # resolve instead of leaving the reader to find sub_gates elsewhere.
                        "gate": {"adopted": primary, "sub_gates": sub_gates},
                        "subgroups": subgroups or {}},
        full_info_regime=scored[FULL_INFO].to_dict(),
        full_info_guard=full_info_guard,
        verdict={
            # A diagnostic/acceptance artifact still computes the gate arithmetic, and a reader
            # skimming the JSON would see `"status": "ADOPT"` and take it for a decision. The
            # loader refuses it, but the file should not need the loader to be read correctly.
            **({} if artifact_kind == VERDICT_KIND else {
                "advisory_only": True,
                "advisory_note": (
                    f"artifact_kind={artifact_kind!r}: this arm CANNOT decide adoption. Its folds "
                    "sit inside the confirmatory window, so acting on the numbers below would be "
                    "a selection leak. The fields are the same arithmetic, not a verdict."
                ),
            }),
            "adopt": adopt,
            "status": status,
            "sub_gates": sub_gates,
            "formula": (
                "serving_regime.gate.adopted AND full_info_guard "
                "AND NOT serving_regime.subgroups.subgroup_guard_status in {FAIL, MISSING}"
            ),
            "primary": primary,
            "min_effect_delta": delta,
            "noninferior_width": ni_width,
            "subgroup_guard": sg_guard,
            "subgroup_guard_status": sg_status,
            "subgroup_assurance": subgroup_assurance,
            "decision_reason": decision_reason,
            "critical_residual_risk": (subgroups or {}).get("critical_residual_risk"),
            "recent_guard": recent,
        },
        subgroups=subgroups or {},
        gate_config=gate_config,
        uncalibrated=uncalibrated,
        notes={
            "regimes": [SERVING, FULL_INFO],
            "both_arms_masked": True,
            "n_valid_races": len(cand_ids),
            #: v3: which year `recent_year_*` refers to, so a report read in a later season is not
            #: silently interpreted against a frozen 2026.
            "target_year": resolve_target_year(cand_valid, gate_config),
            # Surfaced, not buried: without this a reader would see a "serving regime" comparison
            # in which one arm never actually experienced the regime.
            "candidate_is_regime_invariant": regime_invariance_expected,
            **({"regime_invariance_note": (
                "The candidate's serving and full-info scores are bit-identical because it was "
                "trained with every race masked, so it reads no same-day weight at all. That is "
                "what this control arm exists to measure. The active arm IS affected, which is "
                "how this is distinguished from a broken mask (a fault would leave both inert)."
            )} if regime_invariance_expected else {}),
        },
        # DIAGNOSTIC (073 FR-014 / 091 T052). Block-width sensitivity is NEVER ANDed into the gate:
        # the primary estimator stays race_day_cluster_bootstrap_ci_v1, frozen before OOS. Widening
        # the blocks until the CI clears zero — or narrowing them until it does — is exactly the
        # post-hoc estimator choice pre-registration exists to prevent.
        bootstrap_sensitivity={
            name: {
                k: asdict(v)
                for k, v in race_day_cluster_bootstrap_sensitivity_v2(
                    diffs,
                    b=boot.get("b", 2000),
                    seed=boot.get("seed", 20260810),
                    alpha=float(boot.get("alpha", 0.05)),
                ).items()
            }
            for name, diffs in day_diffs.items()
        },
        diffs_by_day=day_diffs,
    )
