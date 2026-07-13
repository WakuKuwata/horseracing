"""Paired candidate↔active evaluation + adoption gate (Feature 068, T016/T017).

Both arms are re-fit per outer fold from their ``PredictorFactory`` (never a saved booster,
codex C1) and scored on the SAME model-blind valid race set (FR-003/C8). PRIMARY is race-level
winner NLL (FR-001); started-all LogLoss/Brier and ECE variants are diagnostics; top2/top3 feed
the non-inferiority gate; a race-day moving-block bootstrap gives the paired-diff CI (FR-004).

The gate (FR-008) reads pre-registered thresholds from gate-config (III); adopting requires
winner-NLL win AND CI-upper<0 AND recent-window (3y AND 5y) non-degradation AND top2/top3
non-inferiority AND calibration non-inferiority (with an absolute-ECE 0.05 emergency stop).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .bootstrap import moving_block_bootstrap_ci
from .dataset import EvalRace, population_masks
from .foldfit import PredictorFactory, predict_over_folds
from .metrics import (
    ece_by_prob_band,
    ece_equal_mass,
    log_loss_label,
    started_all_metrics,
    uniform_baseline_winner_nll,
    winner_nll,
)
from .splits import FIRST_VALID_YEAR

DEFAULT_BAND_EDGES = (0.05, 0.15, 0.30)


class PairedContractError(RuntimeError):
    """Race set / prediction coverage mismatch — fail closed, never silently intersect (C8)."""


def _clip_nll(p: float) -> float:
    return -math.log(min(max(p, 1e-15), 1.0 - 1e-15))


@dataclass
class ArmScores:
    winner_nll: float
    winner_excluded: int
    started_all: dict
    ece_equal_width_like: dict  # equal-mass ECE (tie-safe) on started-all win probs
    ece_by_band: dict
    top2_logloss: float
    top3_logloss: float


@dataclass
class GateResult:
    primary: bool
    stat_guard: bool
    recent_guard: bool
    top_noninferior: bool
    calibration: bool
    adopted: bool
    reasons: dict


@dataclass
class PairedReport:
    candidate_recipe_meta: dict
    active_recipe_meta: dict
    candidate_recipe_hash: str
    active_recipe_hash: str
    race_id_set_hash: str
    n_races: int
    n_eligible: int
    uniform_baseline_winner_nll: float
    periods: dict            # {"all": {...}, "recent_3y": {...}, "recent_5y": {...}}
    bootstrap_ci: dict
    gate: GateResult
    snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _winner_probs(valid_races, preds, *, arm: str):
    """Per-race winner win-prob (None for ineligible races), aligned to valid_races order."""
    out = []
    for er in valid_races:
        pop = population_masks(er)
        if not pop.eligible:
            out.append(None)
            continue
        race_preds = preds[er.context.race_id]
        p = race_preds.get(pop.winner_horse_id)
        out.append(float(p.win) if p is not None else None)
    return out


def _started_all_arrays(valid_races, preds):
    """Flatten per started horse: win/top2/top3 probs + started labels (DNF=0)."""
    win_p, win_y, top2_p, top2_y, top3_p, top3_y, field_sizes = [], [], [], [], [], [], []
    for er in valid_races:
        pop = population_masks(er)
        race_preds = preds[er.context.race_id]
        for hid in pop.started_horse_ids:
            pr = race_preds.get(hid)
            if pr is None:
                raise PairedContractError(
                    f"missing prediction for {hid} in race {er.context.race_id} (fail-closed C8)"
                )
            win_p.append(float(pr.win))
            win_y.append(pop.started_win[hid])
            top2_p.append(float(pr.top2))
            top2_y.append(pop.started_top2[hid])
            top3_p.append(float(pr.top3))
            top3_y.append(pop.started_top3[hid])
            field_sizes.append(pop.field_size)
    return {
        "win": (win_p, win_y), "top2": (top2_p, top2_y),
        "top3": (top3_p, top3_y), "field_sizes": field_sizes,
    }


def _score_arm(valid_races, preds, *, band_edges) -> ArmScores:
    wp = _winner_probs(valid_races, preds, arm="")
    nll, excluded = winner_nll(wp)
    arr = _started_all_arrays(valid_races, preds)
    win_p, win_y = arr["win"]
    return ArmScores(
        winner_nll=nll,
        winner_excluded=excluded,
        started_all=started_all_metrics(win_p, win_y),
        ece_equal_width_like=ece_equal_mass(win_p, win_y),
        ece_by_band=ece_by_prob_band(win_p, win_y, band_edges),
        top2_logloss=log_loss_label(*arr["top2"]),
        top3_logloss=log_loss_label(*arr["top3"]),
    )


def _winner_nll_over(valid_races, preds):
    return winner_nll(_winner_probs(valid_races, preds, arm=""))[0]


def _period_subset(valid_races, *, min_date):
    return [er for er in valid_races if er.context.race_date >= min_date]


def _build_gate(cand: ArmScores, act: ArmScores, ci: dict, recent: dict, cfg: dict) -> GateResult:
    top = cfg.get("top_noninferior", {"top2": 0.0005, "top3": 0.0005})
    cal = cfg.get("calibration", {"noninferior_width": 0.001, "emergency_abs_ece": 0.05})
    primary = cand.winner_nll < act.winner_nll
    ci_upper = ci.get("ci_high")
    stat_guard = ci_upper is not None and ci_upper < 0.0
    recent_guard = recent["pass"]
    top_ni = (
        (cand.top2_logloss - act.top2_logloss) <= top["top2"]
        and (cand.top3_logloss - act.top3_logloss) <= top["top3"]
    )
    cand_ece = cand.ece_equal_width_like["ece"]
    act_ece = act.ece_equal_width_like["ece"]
    emergency = cand_ece >= cal["emergency_abs_ece"]  # absolute stop overrides non-inferiority
    calibration = (not emergency) and ((cand_ece - act_ece) <= cal["noninferior_width"])
    adopted = primary and stat_guard and recent_guard and top_ni and calibration
    return GateResult(
        primary=primary, stat_guard=stat_guard, recent_guard=recent_guard,
        top_noninferior=top_ni, calibration=calibration, adopted=adopted,
        reasons={
            "winner_nll_diff": cand.winner_nll - act.winner_nll,
            "ci_high": ci_upper,
            "top2_diff": cand.top2_logloss - act.top2_logloss,
            "top3_diff": cand.top3_logloss - act.top3_logloss,
            "cand_ece": cand_ece, "act_ece": act_ece,
            "emergency_stop": emergency, "recent": recent,
        },
    )


def paired_eval(
    candidate: PredictorFactory,
    active: PredictorFactory,
    eval_races: list[EvalRace],
    *,
    gate_config: dict | None = None,
    first_valid_year: int = FIRST_VALID_YEAR,
    bootstrap_seed: int = 20260712,
    bootstrap_b: int = 2000,
    num_threads: int | None = None,
    band_edges: tuple[float, ...] = DEFAULT_BAND_EDGES,
    snapshot: dict | None = None,
) -> PairedReport:
    cfg = gate_config or {}
    cand_preds, valid_races = predict_over_folds(
        candidate, eval_races, first_valid_year=first_valid_year, num_threads=num_threads
    )
    act_preds, act_valid = predict_over_folds(
        active, eval_races, first_valid_year=first_valid_year, num_threads=num_threads
    )
    # model-blind fixed race set: both arms MUST cover the identical valid races (C8).
    cand_ids = {er.context.race_id for er in valid_races}
    act_ids = {er.context.race_id for er in act_valid}
    if cand_ids != act_ids:
        raise PairedContractError("candidate/active valid race sets differ (fail-closed, C8)")
    from .hashing import race_set_hash
    race_hash = race_set_hash(cand_ids)

    cand_scores = _score_arm(valid_races, cand_preds, band_edges=band_edges)
    act_scores = _score_arm(valid_races, act_preds, band_edges=band_edges)

    # per-race paired winner-NLL diff, grouped by race-day for the block bootstrap (FR-004).
    diffs_by_day: dict = {}
    n_eligible = 0
    cand_wp = _winner_probs(valid_races, cand_preds, arm="")
    act_wp = _winner_probs(valid_races, act_preds, arm="")
    for er, cp, ap in zip(valid_races, cand_wp, act_wp, strict=True):
        if cp is None or ap is None:
            continue
        n_eligible += 1
        day = er.context.race_date.isoformat()
        diffs_by_day.setdefault(day, []).append(_clip_nll(cp) - _clip_nll(ap))
    boot_cfg = cfg.get("bootstrap", {})
    ci = moving_block_bootstrap_ci(
        diffs_by_day, b=boot_cfg.get("b", bootstrap_b),
        seed=boot_cfg.get("seed", bootstrap_seed),
    )

    # periods: all / recent 3y / recent 5y (FR-005), by the latest valid race_date.
    max_date = max(er.context.race_date for er in valid_races)
    periods: dict = {"all": {
        "candidate": cand_scores.winner_nll, "active": act_scores.winner_nll,
        "diff": cand_scores.winner_nll - act_scores.winner_nll, "n_races": len(valid_races),
    }}
    recent_pass = True
    recent_detail = {}
    for label, years in (("recent_3y", 3), ("recent_5y", 5)):
        min_date = max_date.replace(year=max_date.year - years)
        sub = _period_subset(valid_races, min_date=min_date)
        if not sub:
            periods[label] = {"n_races": 0, "empty": True}
            recent_detail[label] = "empty"
            continue
        c = _winner_nll_over(sub, cand_preds)
        a = _winner_nll_over(sub, act_preds)
        degraded = not (c < a or math.isclose(c, a))
        periods[label] = {"candidate": c, "active": a, "diff": c - a, "n_races": len(sub)}
        recent_detail[label] = {"diff": c - a, "degraded": degraded}
        if degraded:
            recent_pass = False  # conservative AND: any window degrading fails (analyze C2)
    recent = {"pass": recent_pass, "windows": recent_detail}

    gate = _build_gate(cand_scores, act_scores, asdict(ci), recent, cfg)
    return PairedReport(
        candidate_recipe_meta=candidate.recipe_meta,
        active_recipe_meta=active.recipe_meta,
        candidate_recipe_hash=candidate.recipe_hash,
        active_recipe_hash=active.recipe_hash,
        race_id_set_hash=race_hash,
        n_races=len(valid_races),
        n_eligible=n_eligible,
        uniform_baseline_winner_nll=uniform_baseline_winner_nll(
            [population_masks(er).field_size for er in valid_races]
        ),
        periods=periods,
        bootstrap_ci=asdict(ci),
        gate=gate,
        snapshot=snapshot or {},
    )
