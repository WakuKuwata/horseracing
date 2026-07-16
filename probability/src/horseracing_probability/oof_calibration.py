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

    # result-blind transfer check: OOF raw winner-scores vs full-history reference winner-scores.
    ref_scores: list[float] = []
    for race_id, _rd, _p, winner, dead_heat in samples:
        if dead_heat or winner is None:
            continue
        ref = _latest_run_predictions(session, race_id, base_model_version=base_model_version)
        if ref and winner in ref:
            ref_scores.append(float(_norm(ref).get(winner, 0.0)))
    ks = ks_distance(held["raw_winner_scores"], ref_scores)

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
