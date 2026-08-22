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

from .bootstrap import inflate_for_seed_noise, race_day_cluster_bootstrap_ci_v1
from .dataset import EvalRace, population_masks
from .decision import (
    EVALUATION_CONTRACT_VERSION,
    NO_DECISION,
    final_decision,
    gate_config_hash,
)
from .foldfit import PredictorFactory, predict_over_folds
from .gates import evaluate_core_gate, recent_window_guard
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
    """-log(p) with clipping, but only AFTER the value is confirmed to be a probability.

    Clipping first was fail-open: ``p=1.2`` became ``1-1e-15`` and scored a winner NLL of ~0, so a
    broken arm looked like a perfect predictor (2026-08 multi-codex review)."""
    from .metrics import validate_probs
    validate_probs([p], where="paired winner probability")
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
    #: Feature 069 US1: race/horse-level subgroup CIs + intersection-union guard (None unless
    #: paired_eval(subgroups=True)). 068 reports are byte-identical when omitted (FR-005).
    subgroups: dict | None = None
    #: Feature 073 US1: single tri-value machine decision (ADOPT/REJECT/NO_DECISION) folding the
    #: main gate + subgroup guard + eval-window sufficiency, plus audit provenance (FR-001/005).
    decision: str = NO_DECISION
    decision_reason: dict = field(default_factory=dict)
    evaluation_contract_version: str = EVALUATION_CONTRACT_VERSION
    gate_config_hash: str = ""
    #: Feature 073 US3 (FR-014): diagnostic block-width sensitivities (2/3/4-day, week). Empty
    #: unless paired_eval(compute_sensitivity=True). NEVER ANDed into the gate — diagnostic only.
    bootstrap_sensitivity: dict = field(default_factory=dict)
    #: Contract v4: ``bootstrap_ci`` widened by the declared retraining (seed) variance. The gate
    #: reads THIS interval, not the sampling-only one. Both are reported so the two components
    #: stay separable and past artifacts remain comparable.
    total_ci: dict = field(default_factory=dict)
    #: what was added and where the number came from (frozen in the gate-config)
    seed_noise: dict = field(default_factory=dict)
    #: pre-registered opportunity-set comparison (None unless a mask was injected). Adoption
    #: requires superiority HERE and non-inferiority overall — never one without the other.
    opportunity: dict | None = None
    #: Contract v3: the year the ``recent_year_*`` subgroups refer to (window's latest year, or a
    #: gate-config pin). v2 hard-coded 2026, which would have frozen the "current regime" guard.
    target_year: int | None = None
    #: Feature 097: the per-race-day paired diffs (candidate − active winner NLL, day key →
    #: list) that ``bootstrap_ci`` was computed from. Exposed so a driver that evaluates several
    #: disjoint pseudo-worlds can POOL them into one race-day cluster bootstrap instead of
    #: subtracting two per-window intervals (which has no interval). Purely additive: every
    #: pre-097 key of ``to_dict()`` is unchanged.
    diffs_by_day: dict = field(default_factory=dict)

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
    """Flatten per started horse: win/top2/top3 probs + started labels (DNF=0).

    Partial-ingest races are skipped. ``population_masks`` already refuses them for winner NLL,
    but the started-all arrays took them anyway, so a started horse with NO result row at all was
    scored as a real 0 on win/top2/top3 — a fabricated label feeding the top2/top3 non-inferiority
    gate and the ECE gate. Measured incidence on the 2019+ window: 2 races of 26,411 (30 rows of
    362,776), so no recorded verdict moves; the labels were wrong regardless (2026-08 review).
    """
    win_p, win_y, top2_p, top2_y, top3_p, top3_y, field_sizes = [], [], [], [], [], [], []
    for er in valid_races:
        pop = population_masks(er)
        if not pop.complete_results:
            continue
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


def _build_gate(cand: ArmScores, act: ArmScores, ci: dict, recent: dict, cfg: dict,
                opportunity=None) -> GateResult:
    """Adapter onto the shared ``gates.evaluate_core_gate`` (contract v3).

    The conditions themselves now live in ONE place so this path and the regime path cannot drift;
    the legacy 5-field ``GateResult`` shape is preserved for its existing consumers.
    """
    core = evaluate_core_gate(
        diff=cand.winner_nll - act.winner_nll,
        ci_low=ci.get("ci_low"),
        ci_high=ci.get("ci_high"),
        recent=recent,
        top2_diff=cand.top2_logloss - act.top2_logloss,
        top3_diff=cand.top3_logloss - act.top3_logloss,
        cand_ece=cand.ece_equal_width_like["ece"],
        act_ece=act.ece_equal_width_like["ece"],
        cfg=cfg,
        opportunity=opportunity,
    )
    return GateResult(
        primary=core.primary, stat_guard=core.stat_guard, recent_guard=core.recent,
        top_noninferior=core.top_noninferior, calibration=core.calibration,
        adopted=core.adopted,
        reasons={**core.reasons, "sub_gates": core.sub_gates},
    )


def _horse_logloss(p: float, y: int) -> float:
    p = min(max(p, 1e-15), 1.0 - 1e-15)
    return -(math.log(p) if y == 1 else math.log(1.0 - p))


def _ci_and_decision(diffs_by_day, uniform_by_day, *, b, seed, margin, alpha=0.05):
    """Bootstrap CI + four-state decision + subgroup-internal cand−uniform for one subgroup."""
    from .subgroups import residual_risk, three_way
    ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day, b=b, seed=seed, alpha=alpha)
    d = asdict(ci)
    # percentile intervals are asymmetric -> judge precision on the upper arm, not the half-width
    decision = three_way(d.get("ci_low"), d.get("ci_high"), margin, point=d.get("point"))
    all_u = [u for us in uniform_by_day.values() for u in us]
    cand_minus_uniform = (sum(all_u) / len(all_u)) if all_u else None
    return {
        "bootstrap_ci": d, "decision": decision, "margin": margin,
        "n_days": d.get("n_days"), "cand_minus_uniform": cand_minus_uniform,
        # "no FAIL" is not "no harm": for an inconclusive subgroup this is the worst degradation
        # still admitted by the interval. None when the subgroup concluded.
        "residual_risk": residual_risk(d.get("ci_high"), decision),
    }


def resolve_target_year(valid_races, cfg: dict | None = None) -> int:
    """The year the ``recent_year_*`` subgroups refer to (contract v3).

    v2 hard-coded 2026, so from 2027 on the "current regime" guard would have silently kept
    testing a frozen past year. The window's latest year is the default; a gate-config may pin
    ``subgroup_guard.target_year`` when a pre-registration wants the year frozen.
    """
    pinned = ((cfg or {}).get("subgroup_guard", {}) or {}).get("target_year")
    if pinned is not None:
        return int(pinned)
    if not valid_races:
        from .subgroups import _TARGET_YEAR
        return _TARGET_YEAR
    return max(er.context.race_date.year for er in valid_races)


def _compute_subgroups(
    valid_races, cand_preds, act_preds, cand_wp, act_wp, cfg, *,
    obs_count=None, b=2000, seed=20260712, alpha=0.05, target_year=None,
) -> dict:
    """Race-level (winner NLL) + horse-level (started-all per-horse logloss) subgroup CIs + the
    intersection-union three-way guard (FR-001/002/003, codex C1/C2/C3/C6). ``obs_count`` maps
    (race_id, horse_id) -> strictly-before market-obs count for coverage bands (None = omit)."""
    from .subgroups import (
        horse_subgroup_labels,
        is_nk,
        race_subgroup_labels,
        subgroup_guard,
        subgroup_guard_status,
    )
    sg = cfg.get("subgroup_guard", {})
    m_win = sg.get("non_inferior_margin_winner_nll", 0.005)
    m_horse = sg.get("non_inferior_margin_horse_logloss", 0.001)
    critical = sg.get("critical_subgroups", ["2026_only", "nk", "2026_nk"])
    if target_year is None:
        target_year = resolve_target_year(valid_races, cfg)

    # race-level: per-race winner-NLL paired diff, grouped by (subgroup -> day)
    race_diffs: dict = {}
    race_unif: dict = {}
    for er, cp, ap in zip(valid_races, cand_wp, act_wp, strict=True):
        if cp is None or ap is None:
            continue
        yr = er.context.race_date.year
        field_has_nk = any(is_nk(h.horse_id) for h in er.context.started_horses)
        day = er.context.race_date.isoformat()
        n = len(er.context.started_horses)
        u = math.log(n) if n > 0 else 0.0  # uniform winner NLL = -log(1/N) = log(N)
        for lab in race_subgroup_labels(yr, field_has_nk, target_year=target_year):
            race_diffs.setdefault(lab, {}).setdefault(day, []).append(_clip_nll(cp) - _clip_nll(ap))
            race_unif.setdefault(lab, {}).setdefault(day, []).append(_clip_nll(cp) - u)

    # horse-level: per started-horse started-all logloss paired diff, grouped by (subgroup -> day)
    horse_diffs: dict = {}
    horse_unif: dict = {}
    for er in valid_races:
        pop = population_masks(er)
        yr = er.context.race_date.year
        day = er.context.race_date.isoformat()
        cpreds, apreds = cand_preds[er.context.race_id], act_preds[er.context.race_id]
        n = pop.field_size
        u_p = 1.0 / n if n > 0 else 0.5
        for hid in pop.started_horse_ids:
            y = pop.started_win[hid]
            cll = _horse_logloss(float(cpreds[hid].win), y)
            dc = cll - _horse_logloss(float(apreds[hid].win), y)
            du = cll - _horse_logloss(u_p, y)
            oc = obs_count.get((er.context.race_id, hid)) if obs_count else None
            for lab in horse_subgroup_labels(hid, yr, obs_count=oc, target_year=target_year):
                horse_diffs.setdefault(lab, {}).setdefault(day, []).append(dc)
                horse_unif.setdefault(lab, {}).setdefault(day, []).append(du)

    race_out = {
        lab: _ci_and_decision(d, race_unif[lab], b=b, seed=seed, margin=m_win, alpha=alpha)
        for lab, d in race_diffs.items()
    }
    horse_out = {
        lab: _ci_and_decision(d, horse_unif[lab], b=b, seed=seed, margin=m_horse, alpha=alpha)
        for lab, d in horse_diffs.items()
    }
    decisions = {lab: v["decision"] for lab, v in {**race_out, **horse_out}.items()}
    guard_pass = subgroup_guard(decisions, critical)
    status = subgroup_guard_status(decisions, critical)
    return {
        "race_subgroups": race_out, "horse_subgroups": horse_out,
        "critical": critical,
        #: strict intersection-union — "was FULL assurance achieved" (audit field)
        "subgroup_guard": guard_pass,
        #: contract v3 veto input — only FAIL (confidently worse) or MISSING (never computed)
        #: blocks adoption; an untestable subgroup is disclosed, not treated as harm.
        "subgroup_guard_status": status,
        "target_year": target_year,
        "subgroup_decisions": {c: decisions.get(c, "MISSING") for c in critical},
        #: per critical subgroup, the worst degradation its interval still admits (None once the
        #: subgroup concluded). Adoption under NOT_PROVEN must be read against these numbers.
        "critical_residual_risk": {
            c: ({**race_out, **horse_out}.get(c) or {}).get("residual_risk") for c in critical
        },
    }


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
    subgroups: bool = False,
    obs_count: dict | None = None,
    compute_sensitivity: bool = False,
    valid_from=None,
    opportunity_races: set | None = None,
) -> PairedReport:
    cfg = gate_config or {}
    # ``valid_from`` narrows the SCORED races to a day-exact window. Without it a window starting
    # mid-year silently scored the whole year (2026-08 review).
    cand_preds, valid_races = predict_over_folds(
        candidate, eval_races, first_valid_year=first_valid_year, num_threads=num_threads,
        valid_from=valid_from,
    )
    act_preds, act_valid = predict_over_folds(
        active, eval_races, first_valid_year=first_valid_year, num_threads=num_threads,
        valid_from=valid_from,
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
    # The gate-config's alpha was previously read by nobody (only b/seed were passed), so a
    # config declaring a non-default alpha silently got 0.05 (2026-07 multi-codex review).
    boot_alpha = float(boot_cfg.get("alpha", 0.05))
    ci = race_day_cluster_bootstrap_ci_v1(
        diffs_by_day, b=boot_cfg.get("b", bootstrap_b),
        seed=boot_cfg.get("seed", bootstrap_seed), alpha=boot_alpha,
    )
    # Feature 073 US3 (FR-014): diagnostic-only block-width sensitivities (never gate the decision).
    bootstrap_sensitivity: dict = {}
    if compute_sensitivity:
        from .bootstrap import race_day_cluster_bootstrap_sensitivity_v2
        bootstrap_sensitivity = {
            k: asdict(v) for k, v in race_day_cluster_bootstrap_sensitivity_v2(
                diffs_by_day, b=boot_cfg.get("b", bootstrap_b),
                seed=boot_cfg.get("seed", bootstrap_seed), alpha=boot_alpha,
            ).items()
        }

    # periods: all / recent 3y / recent 5y (FR-005), by the latest valid race_date.
    max_date = max(er.context.race_date for er in valid_races)
    periods: dict = {"all": {
        "candidate": cand_scores.winner_nll, "active": act_scores.winner_nll,
        "diff": cand_scores.winner_nll - act_scores.winner_nll, "n_races": len(valid_races),
    }}
    from .gates import DEFAULT_RECENT_WINDOWS, _window_start
    years_list = tuple(
        (cfg.get("recent_guard", {}) or {}).get("windows_years", DEFAULT_RECENT_WINDOWS)
    )
    for years in years_list:
        label = f"recent_{years}y"
        sub = _period_subset(valid_races, min_date=_window_start(max_date, int(years)))
        if not sub:
            periods[label] = {"n_races": 0, "empty": True}
            continue
        c = _winner_nll_over(sub, cand_preds)
        a = _winner_nll_over(sub, act_preds)
        periods[label] = {"candidate": c, "active": a, "diff": c - a, "n_races": len(sub)}
    # Contract v3: the recent-window guard is a non-inferiority test on the SAME per-day paired
    # diffs and the SAME bootstrap as the primary CI — not a zero-tolerance sign test on the point
    # estimate (which failed ~60% of the time under the null and was mapped to REJECT).
    recent = recent_window_guard(diffs_by_day, cfg=cfg, max_date=max_date)

    # Contract v4: the cluster bootstrap resamples RACES and is blind to the variation from
    # refitting the model (measured 2026-08-18: fold-level SD 0.001816 against a same-fold
    # sampling SE of 0.002239). Left out, the interval is ~20% too narrow and the gate's effective
    # false-positive rate is 5.8%, not the nominal 2.5%. The gate reads the combined interval.
    sn = (cfg.get("seed_noise") or {})
    n_folds = len({er.context.race_date.year for er in valid_races})
    total = inflate_for_seed_noise(
        ci, sd_fold=float(sn.get("sd_fold", 0.0)), n_folds=n_folds,
        k_seeds=int(sn.get("k_seeds", 1)), alpha=boot_alpha,
    )
    seed_noise_info = {
        "sd_fold": sn.get("sd_fold"), "k_seeds": int(sn.get("k_seeds", 1)),
        "n_folds": n_folds, "source": sn.get("source"),
        "applied": total.ci_low != ci.ci_low or total.ci_high != ci.ci_high,
    }
    # 事前登録した適用集合(opportunity set)。全体平均は狭い特徴の効果を被覆率で割ってしまうので、
    # 「効くところで効いているか」を別に測る。単独では採用にできない(全体非劣性と AND)。
    opportunity = None
    if opportunity_races is not None:
        from .opportunity import score_opportunity
        opportunity = score_opportunity(
            valid_races, cand_preds, act_preds, races=opportunity_races, cfg=cfg,
            clip_nll=_clip_nll, b=boot_cfg.get("b", bootstrap_b),
            seed=boot_cfg.get("seed", bootstrap_seed), alpha=boot_alpha,
            sd_fold=float(sn.get("sd_fold", 0.0)), k_seeds=int(sn.get("k_seeds", 1)),
        )
    gate = _build_gate(cand_scores, act_scores, asdict(total), recent, cfg, opportunity)
    target_year = resolve_target_year(valid_races, cfg)
    sg = None
    if subgroups:
        sg = _compute_subgroups(
            valid_races, cand_preds, act_preds, cand_wp, act_wp, cfg,
            obs_count=obs_count, b=boot_cfg.get("b", bootstrap_b),
            seed=boot_cfg.get("seed", bootstrap_seed), alpha=boot_alpha,
            target_year=target_year,
        )
    # Feature 073 US1: one machine-decided tri-value verdict (FR-001/002). n_days from the
    # primary CI drives the eval-window sufficiency check (empty/short window -> NO_DECISION).
    decision, decision_reason = final_decision(gate, sg, n_days=ci.n_days, cfg=cfg)
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
        total_ci=asdict(total),
        seed_noise=seed_noise_info,
        opportunity=(opportunity.to_dict() if opportunity is not None else None),
        gate=gate,
        snapshot=snapshot or {},
        subgroups=sg,
        decision=decision,
        decision_reason=decision_reason,
        evaluation_contract_version=EVALUATION_CONTRACT_VERSION,
        gate_config_hash=gate_config_hash(cfg),
        bootstrap_sensitivity=bootstrap_sensitivity,
        target_year=target_year,
        diffs_by_day=diffs_by_day,
    )
