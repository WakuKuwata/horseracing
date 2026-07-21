"""Feature 074 US3: OOF-faithful re-validation of two-gamma (048) calibration.

The calibration samples come from a recipe-faithful OOF bundle (``load_p_samples_from_oof``), NOT
the leaky latest persisted run. Two-gamma is fit **prequentially** — for each fold (valid year)
after the first, the calibrator is fit on PRIOR folds only and applied to the held-out fold, so
the fit fold is never in the evaluation set (FR-009). The calibrated-stage ECE is therefore
measured on strictly-later OOF blocks (FR-010). A result-blind KS transfer check compares the OOF
raw win-score distribution against the full-history reference distribution; a mismatch forces
NO_DECISION (research D8 / gate-config). The verdict re-measures 048 adoption on OOF and is
tri-value — ADOPT / REJECT / NO_DECISION (FR-011). Output is an append-only evaluation artifact
(``evaluation_contract_version=v2``) that 073 FR-007 references; 073 verdicts are never rewritten.

Pure functions (``prequential_held_out``, ``ece``, ``ks_distance``, ``three_way_verdict``) are
DB-free and unit-tested; ``calibrate_oof`` is the thin DB orchestration.
"""

from __future__ import annotations

from horseracing_eval.hashing import stable_hash
from sqlalchemy.orm import Session

from .model_calibration import (
    TWO_GAMMA_PIVOT,
    _latest_run_predictions,
    _norm,
    apply_p_calibrator,
    fit_p_calibrator,
    load_p_samples_from_oof,
)

EVALUATION_CONTRACT_VERSION = "v2"
ADOPT = "ADOPT"
REJECT = "REJECT"
NO_DECISION = "NO_DECISION"


def ece(probs: list[float], labels: list[int], *, bins: int = 10) -> float:
    """Equal-width reliability ECE over flattened (win prob, win label) pairs."""
    if not probs:
        return 0.0
    n = len(probs)
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        last = b == bins - 1
        idx = [i for i in range(n) if (lo <= probs[i] < hi) or (last and probs[i] == 1.0)]
        if not idx:
            continue
        mean_p = sum(probs[i] for i in idx) / len(idx)
        mean_y = sum(labels[i] for i in idx) / len(idx)
        total += (len(idx) / n) * abs(mean_p - mean_y)
    return total


def ks_distance(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov–Smirnov distance (max |CDF_a − CDF_b|). Empty side => 0.0."""
    if not a or not b:
        return 0.0
    grid = sorted(set(a) | set(b))
    sa, sb = sorted(a), sorted(b)

    def cdf(sorted_vals, x):
        # fraction of values <= x
        lo, hi = 0, len(sorted_vals)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_vals[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(sorted_vals)

    return max(abs(cdf(sa, x) - cdf(sb, x)) for x in grid)


def prequential_held_out(
    fold_to_samples: dict[int, list[tuple[dict[str, float], str]]],
    *,
    base_model_version: str | None = None,
    pivot: float = TWO_GAMMA_PIVOT,
) -> dict:
    """Fit two-gamma on PRIOR folds, apply to each held-out fold; collect strictly-later OOF pairs.

    ``fold_to_samples`` maps a fold key (valid year) to a list of ``(p_dict, winner)``. Returns
    flattened per-horse (raw prob, calibrated prob, win label) held-out arrays + the last fitted
    (gamma_lo, gamma_hi) and the raw winner-score list (for the transfer check).
    """
    years = sorted(fold_to_samples)
    raw_probs: list[float] = []
    cal_probs: list[float] = []
    labels: list[int] = []
    raw_winner_scores: list[float] = []
    last_params = {"gamma_lo": 1.0, "gamma_hi": 1.0, "pivot": pivot}
    for i, year in enumerate(years):
        if i == 0:
            continue  # first fold has no prior OOF to fit on
        prior = [s for py in years[:i] for s in fold_to_samples[py]]
        cal = fit_p_calibrator(prior, method="two_gamma", base_model_version=base_model_version)
        if cal.method == "two_gamma":
            last_params = dict(cal.params)
        for p, winner in fold_to_samples[year]:
            pn = _norm(p)
            pc = apply_p_calibrator(p, cal)
            for horse, prob in pn.items():
                raw_probs.append(float(prob))
                cal_probs.append(float(pc[horse]))
                labels.append(1 if horse == winner else 0)
            if winner in pn:
                raw_winner_scores.append(float(pn[winner]))
    return {
        "raw_probs": raw_probs,
        "cal_probs": cal_probs,
        "labels": labels,
        "last_params": last_params,
        "raw_winner_scores": raw_winner_scores,
        "n_held_out_folds": max(0, len(years) - 1),
    }


def three_way_verdict(
    raw_ece: float,
    cal_ece: float,
    *,
    ks: float,
    n_days: int,
    gate_config: dict,
) -> tuple[str, dict]:
    """Tri-value 048-on-OOF verdict. Result-blind gates come first (transfer / sufficiency)."""
    verdict_cfg = (gate_config or {}).get("verdict", {})
    margin = float(verdict_cfg.get("non_inferior_margin_ece", 0.001))
    min_days = int(verdict_cfg.get("no_decision_min_days", 10))
    transfer_cfg = (gate_config or {}).get("transfer_check", {})
    ks_max = float(transfer_cfg.get("ks_distance_max", 0.10))

    if n_days < min_days:
        return NO_DECISION, {"cause": "insufficient_eval_days", "n_days": n_days}
    if ks > ks_max:
        return NO_DECISION, {"cause": "transfer_check_mismatch", "ks": ks, "ks_max": ks_max}
    delta = cal_ece - raw_ece  # negative => calibration improves reliability
    if delta < -margin:
        return ADOPT, {"cause": "calibrated_better", "delta_ece": delta}
    if delta > margin:
        return REJECT, {"cause": "calibrated_worse", "delta_ece": delta}
    return NO_DECISION, {"cause": "within_margin", "delta_ece": delta}


def _gate_config_hash(cfg: dict) -> str:
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    return stable_hash(strip(cfg or {}))


def calibrate_oof(
    session: Session,
    bundle: dict,
    *,
    gate_config: dict,
    base_model_version: str = "lgbm-063",
) -> dict:
    """OOF-faithful two-gamma re-validation for the ``two_gamma_win`` stage (FR-009/010/011/012).

    Returns an append-only evaluation artifact (``evaluation_contract_version=v2``) referencing the
    OOF bundle. Does NOT touch production or any 073 verdict.
    """
    samples = load_p_samples_from_oof(session, bundle)
    fold_to_samples: dict[int, list] = {}
    days: set = set()
    n_dead_heat = 0
    for _race_id, race_date, p_dict, winner, dead_heat in samples:
        if dead_heat or winner is None:
            n_dead_heat += 1 if dead_heat else 0
            continue  # dead heats excluded from calibration (only labels)
        fold_to_samples.setdefault(race_date.year, []).append((p_dict, winner))
        days.add(race_date.isoformat())

    held = prequential_held_out(fold_to_samples, base_model_version=base_model_version)
    raw_ece = ece(held["raw_probs"], held["labels"])
    cal_ece = ece(held["cal_probs"], held["labels"])
    # A single fold has no prior OOF to fit on => empty held-out block; there is no calibrated-stage
    # evidence, so the verdict is NO_DECISION with an honest cause (not the within-margin default).
    _no_held_out = held["n_held_out_folds"] == 0 or not held["labels"]

    # result-blind transfer check: OOF raw winner-scores vs full-history reference winner-scores.
    ref_scores: list[float] = []
    for race_id, _rd, _p, winner, dead_heat in samples:
        if dead_heat or winner is None:
            continue
        ref = _latest_run_predictions(session, race_id, base_model_version=base_model_version)
        if ref and winner in ref:
            ref_scores.append(float(_norm(ref).get(winner, 0.0)))
    ks = ks_distance(held["raw_winner_scores"], ref_scores)

    if _no_held_out:
        verdict = NO_DECISION
        reason = {"cause": "no_held_out_folds", "n_folds": len(fold_to_samples)}
    else:
        verdict, reason = three_way_verdict(
            raw_ece, cal_ece, ks=ks, n_days=len(days), gate_config=gate_config
        )

    return {
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "stage": "two_gamma_win",
        "base_model_version": base_model_version,
        "bundle_digest": bundle.get("bundle_digest"),
        "fit": {
            "gamma_lo": held["last_params"].get("gamma_lo"),
            "gamma_hi": held["last_params"].get("gamma_hi"),
            "pivot": held["last_params"].get("pivot", TWO_GAMMA_PIVOT),
            "n_held_out_folds": held["n_held_out_folds"],
        },
        "ece": {"raw": raw_ece, "calibrated": cal_ece, "delta": cal_ece - raw_ece},
        "transfer_check": {"ks": ks, "reference": "full_history_latest_run"},
        "verdict": verdict,
        "verdict_reason": reason,
        "n_eval_days": len(days),
        "n_dead_heat_excluded": n_dead_heat,
        "gate_config_hash": _gate_config_hash(gate_config),
        # Feature 074 T031 / research D7: the same generation-unfiltered latest-run leak exists in
        # the 066 dispersion two-gamma and the joint calibration loaders. 074 is evidence-only and
        # does NOT rewire them; that correction is deferred to Feature 076 (activation & parity).
        "diagnostics": {
            "same_type_leak_deferred_to_076": [
                "training.cli dispersion two-gamma (066) uses the generation-unfiltered loader",
                "probability.calibration joint calibration uses a generation-unfiltered latest run",
            ],
            "development_evidence": "2008-2026 OOF ECE is development evidence, not confirmatory",
        },
    }


class _BundlePredictor:
    """Feature 078 US1: an eval ``Predictor`` backed by the OOF bundle's stored per-race preds.

    ``fit`` is a no-op — the OOF predictions are ALREADY the fold-fit output (the saved booster is
    never applied to past races, 074 C1), so there is nothing to re-fit. ``predict_race`` returns
    the bundle's win/top2/top3 for the race. Reusing this with the pre-registered 049
    ``evaluate_stage_discount`` gives the SAME hardened stage gate (top2+top3 LogLoss/ECE improve +
    fold-majority + worst-fold guard, research D3) with the λ fit prequentially on RAW OOF win
    (research D1: serving applies λ to raw p, so the OOF λ is fit on raw win — no two-gamma)."""

    is_leaky_reference = False

    def __init__(self, bundle: dict) -> None:
        self._preds = bundle["predictions"]

    def fit(self, train_races) -> None:  # noqa: D401 - no-op (predictions are pre-computed OOF)
        return None

    def predict_race(self, race):
        from horseracing_eval.predictor import Prediction
        raw = self._preds.get(race.race_id, {})
        return {
            hid: Prediction(win=float(v["win"]), top2=float(v["top2"]), top3=float(v["top3"]))
            for hid, v in raw.items()
        }


def calibrate_stage_oof(
    session: Session,
    bundle: dict,
    *,
    gate_config: dict | None = None,
    base_model_version: str = "lgbm-063",
    min_held_out_folds: int = 1,
    min_races: int | None = None,
    eval_races=None,
) -> dict:
    """Feature 078 US1/US2: OOF-faithful re-validation of the stage-discount λ (top2/top3).

    Runs the pre-registered 049 walk-forward gate over a bundle-backed predictor, so λ2/λ3 are fit
    prequentially on RAW OOF win (D1) and the top2/top3 LogLoss/ECE improvement + fold-majority +
    worst-fold guard verdict is the SAME contract 049 uses (D3). Returns an append-only evaluation
    artifact whose verdict is tri-value (D6): ADOPT when the gate adopts, REJECT when it evaluated a
    candidate that did not beat baseline, NO_DECISION when there is no strictly-later held-out block
    (or every fold fell back to identity for want of samples). The ``prequential`` fold λ are EVAL
    params, NOT the shipped params — the deployment final-fit (all-OOF, D2) is a separate step.

    Results are used only as scoring labels (憲法 II); win is identical by construction (candidate
    reuses the baseline win vector), so this never moves win.
    """
    from horseracing_eval.dataset import load_eval_races
    from horseracing_eval.splits import FIRST_VALID_YEAR
    from horseracing_eval.stage_discount import DEFAULT_MIN_RACES
    from horseracing_eval.stage_discount_eval import evaluate_stage_discount

    covered = set(bundle["predictions"])
    races = eval_races if eval_races is not None else load_eval_races(session)
    races = [er for er in races if er.context.race_id in covered]
    report = evaluate_stage_discount(
        _BundlePredictor(bundle), races,
        first_valid_year=FIRST_VALID_YEAR,
        min_races=DEFAULT_MIN_RACES if min_races is None else min_races,
    )

    # a fold contributes real candidate evidence only when a NON-identity λ was actually fit (the
    # first fold and any under-sampled / fallback fold ship λ=1.0 = identity = no candidate).
    fitted = [
        fl for fl in report.fold_lambdas
        if fl["lambda2"] != 1.0 or fl["lambda3"] != 1.0
    ]
    if report.n_folds <= min_held_out_folds or not fitted:
        verdict, reason = NO_DECISION, {
            "cause": "no_held_out_stage_evidence",
            "n_folds": report.n_folds, "n_fitted_folds": len(fitted),
        }
    elif report.adopted:
        verdict, reason = ADOPT, {"primary_pass": True, "guard_pass": True, "win_identical": True}
    else:
        verdict, reason = REJECT, {
            "primary_pass": report.primary_pass, "guard_pass": report.guard_pass,
            "win_identical": report.win_identical,
        }

    last = fitted[-1] if fitted else {"lambda2": 1.0, "lambda3": 1.0}
    return {
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "stage": "stage_discount_topk",
        "consumer_pipeline": "serving_raw",  # D1: λ applied to RAW win (serving display path)
        "base_model_version": base_model_version,
        "bundle_digest": bundle.get("bundle_digest"),
        # prequential (EVAL) λ — NOT the shipped params (deployment final-fit is D2/T006)
        "prequential": {
            "fold_lambdas": report.fold_lambdas,
            "last_lambda2": last["lambda2"], "last_lambda3": last["lambda3"],
        },
        "metrics": {
            "top2": {"baseline": report.baseline["top2"], "candidate": report.candidate["top2"]},
            "top3": {"baseline": report.baseline["top3"], "candidate": report.candidate["top3"]},
            "win_max_abs_diff": report.win_max_abs_diff,
            "winning_folds_top3": report.winning_folds_top3,
            "worst_fold_top3_dloss": report.worst_fold_top3_dloss,
        },
        "gate": {
            "primary_pass": report.primary_pass, "guard_pass": report.guard_pass,
            "win_identical": report.win_identical, "n_folds": report.n_folds,
        },
        "verdict": verdict,
        "verdict_reason": reason,
        "gate_config_hash": _gate_config_hash(gate_config),
    }


# --- Feature 078 US2 (T006): deployment final-fit (D2) ----------------------
# The prequential fit is for the VERDICT (each fold's prior-only params). The SHIPPED params come
# from a separate fit over ALL eligible OOF samples, gated on the verdict: a non-ADOPT stage
# explicit identity (research D6). fit_through = the last date the shipped params saw (D5).

def _identity_stage_deployment() -> dict:
    return {"lambda2": 1.0, "lambda3": 1.0, "fit_through": None,
            "fit_race_set_hash": None, "n_fit": 0, "fallback": False, "identity": True}


def stage_deployment_fit(
    session: Session, bundle: dict, *, adopt: bool, min_races: int | None = None, eval_races=None,
) -> dict:
    """D2: fit stage λ on ALL OOF samples (raw win, D1) for deployment, gated by the verdict.

    ``adopt=False`` (REJECT / NO_DECISION) → explicit identity, no fit_through. ``adopt=True`` → the
    fitted λ + provenance (fit_through = max contributing race_date, fit_race_set_hash, n_fit). If
    the all-OOF fit itself falls back to identity (should not happen when prior folds fit), the
    identity is shipped and flagged — the manifest policy (D9) keeps eligibility consistent."""
    if not adopt:
        return _identity_stage_deployment()
    from horseracing_eval.stage_discount import DEFAULT_MIN_RACES, fit_stage_discount

    from .model_calibration import load_topk_samples_from_oof, to_topk_samples

    raw = load_topk_samples_from_oof(session, bundle)
    samples = to_topk_samples(raw, calibrator=None)  # calibrator=None → RAW win (D1)
    sd = fit_stage_discount(
        samples, min_races=DEFAULT_MIN_RACES if min_races is None else min_races)
    used = [(rid, rd) for (rid, rd, p, placed) in raw if placed[0] is not None and placed[0] in p]
    fit_through = max((rd for _r, rd in used), default=None)
    race_ids = sorted(rid for rid, _ in used)
    return {
        "lambda2": sd.lambda2, "lambda3": sd.lambda3,
        "fit_through": fit_through.isoformat() if fit_through else None,
        "fit_race_set_hash": stable_hash(race_ids), "n_fit": len(race_ids),
        "fallback": sd.fallback, "identity": sd.is_identity,
    }


def _identity_two_gamma_deployment() -> dict:
    return {"gamma_lo": 1.0, "gamma_hi": 1.0, "pivot": TWO_GAMMA_PIVOT, "fit_through": None,
            "fit_race_set_hash": None, "n_fit": 0, "identity": True}


def two_gamma_deployment_fit(
    session: Session, bundle: dict, *, adopt: bool, base_model_version: str = "lgbm-063",
) -> dict:
    """D2: fit two-gamma γ on ALL OOF (p, winner) samples for deployment, gated by the verdict.

    ``adopt=False`` → explicit identity. ``adopt=True`` → fitted γ + provenance. An under-sampled
    fit returns method='identity' (γ=1) — shipped as identity, flagged."""
    if not adopt:
        return _identity_two_gamma_deployment()
    samples = load_p_samples_from_oof(session, bundle)
    pairs = [(p, w) for (_rid, _rd, p, w, dh) in samples if not dh and w is not None]
    cal = fit_p_calibrator(pairs, method="two_gamma", base_model_version=base_model_version)
    used = [(rid, rd) for (rid, rd, _p, w, dh) in samples if not dh and w is not None]
    fit_through = max((rd for _r, rd in used), default=None)
    race_ids = sorted(rid for rid, _ in used)
    g = cal.params
    return {
        "gamma_lo": float(g.get("gamma_lo", 1.0)), "gamma_hi": float(g.get("gamma_hi", 1.0)),
        "pivot": float(g.get("pivot", TWO_GAMMA_PIVOT)),
        "fit_through": fit_through.isoformat() if fit_through else None,
        "fit_race_set_hash": stable_hash(race_ids), "n_fit": len(race_ids),
        "identity": cal.method != "two_gamma",
    }
