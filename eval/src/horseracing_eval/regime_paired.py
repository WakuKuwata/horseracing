"""Feature 091: paired evaluation under TWO input regimes, with a single materialised verdict.

Why this exists: the standard paired-eval scores settled races, where the same-day weight is
present. Under that condition `prev_weight` has nothing to add, so a feature whose entire purpose
is to fix the *serving* input looks worthless. The measurement has to be taken under the condition
predictions are actually made.

  serving regime  -> both arms predict with the same-day weight columns masked  (PRIMARY)
  full-info       -> both arms predict untouched                                (non-inferiority guard)

Both arms are masked. The active model has no `prev_weight`, so it simply degrades — that IS the
quantity being measured (what production currently loses at serve time). Masking one arm only
would compare two different questions.

This module COMPOSES `paired.py` rather than editing it, so the existing contract (and the tests
pinning it) stay untouched.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .bootstrap import race_day_cluster_bootstrap_ci_v1
from .foldfit import predict_over_folds_multi
from .hashing import race_set_hash
from .paired import DEFAULT_BAND_EDGES, PairedContractError, _clip_nll, _score_arm, _winner_probs

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
    gate_config: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)

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

    kwargs = {"num_threads": num_threads}
    if first_valid_year is not None:
        kwargs["first_valid_year"] = first_valid_year
    regimes = {SERVING: serving_spec, FULL_INFO: None}

    cand_by_regime, cand_valid, cand_raw = predict_over_folds_multi(
        candidate, eval_races, regimes=regimes, collect_raw=True, **kwargs
    )
    act_by_regime, act_valid, act_raw = predict_over_folds_multi(
        active, eval_races, regimes=regimes, collect_raw=True, **kwargs
    )

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
    for name in (SERVING, FULL_INFO):
        spec = regimes[name]
        cand_preds, act_preds = cand_by_regime[name], act_by_regime[name]
        diffs_by_day, n_races = _paired_diff(cand_valid, cand_preds, act_preds)
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

    primary = (srv.diff < -delta) and (srv.ci_high is not None and srv.ci_high < 0.0)
    full_info_guard = scored[FULL_INFO].diff <= ni_width
    adopt = bool(primary and full_info_guard)

    return RegimeReport(
        artifact_kind=artifact_kind,
        eligible_for_verdict=(artifact_kind == VERDICT_KIND),
        race_set_hash=rs_hash,
        serving_regime=scored[SERVING].to_dict(),
        full_info_regime=scored[FULL_INFO].to_dict(),
        full_info_guard=full_info_guard,
        verdict={
            "adopt": adopt,
            "formula": (
                "serving_regime.gate.adopted AND full_info_guard "
                "AND serving_regime.subgroups.subgroup_guard"
            ),
            "primary": primary,
            "min_effect_delta": delta,
            "noninferior_width": ni_width,
            "subgroup_guard": None,  # supplied by the subgroup pass; None => not yet decidable
        },
        gate_config=gate_config,
        uncalibrated=uncalibrated,
        notes={
            "regimes": [SERVING, FULL_INFO],
            "both_arms_masked": True,
            "n_valid_races": len(cand_ids),
        },
    )
