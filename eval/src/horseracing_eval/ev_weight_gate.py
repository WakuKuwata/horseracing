"""Feature 079: paired EV-weight adoption gate — PURE SCORER (self-contained).

Judges a market-aware EV-weighted candidate against the unweighted baseline on the SAME
walk-forward OOS race set. This is NOT the 064 policy_gate (which compares one model's uncapped
vs capped policy and has no CI) — codex #2: an ROI-oriented candidate needs an ACTIVE-vs-CANDIDATE
*paired* comparison with a cluster-bootstrap CI on the recovery difference.

Inputs = two per-horse row lists (baseline, candidate), each row a dict with keys
``race_id, year, race_day, p (calibrated win prob), odds (closing), won (0/1)``. Both arms are
scored under the SAME fixed pre-registered policy (bet WIN where renorm-EV >= threshold and
odds < cap; flat stake), identical to betting.ev / policy_gate semantics.

PRIMARY estimand = paired recovery difference  delta = recovery(cand) - recovery(base),
recovery = sum(odds if won) / n_bets. Because the arms place DIFFERENT numbers of bets, recovery
is a RATIO of sums; the CI therefore resamples whole RACE-DAYS and RECOMPUTES each arm's ratio
inside every replicate (codex #2) — NOT a mean-of-per-race-diffs bootstrap (that is the v1 helper
in bootstrap.py, which is wrong for a ratio estimand with unequal bet counts).

MUST guards (fail => REJECT regardless of delta): winner-NLL non-inferiority (improvement NOT
required) and tail-calibration non-degradation on the odds>=cap and q<0.05 masks (047: OOF p is
already ~3.5x over-confident in the longshot tail, and the weight up-weights that region).

Absolute recovery > 1 is NOT the bar (closing-oracle bias); only the relative paired delta is
interpreted, and even ADOPT means prospective evaluation, never shipping (079 pre-registration).

Imports NEITHER horseracing_betting NOR horseracing_training (eval stays predictor-agnostic and
avoids the betting->eval cycle). The win-only selection predicate is re-used from policy_gate.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .policy_gate import _bets_for_race

#: pre-registered verdict thresholds (079 sec 3/4). FIXED — never chosen from these results.
DEFAULT_CAP = 21.0
DEFAULT_THRESHOLD = 1.0
DEFAULT_SEED = 20260722
DEFAULT_B = 10_000
DEFAULT_NLL_TOL = 0.005        # cand winner NLL must be <= base + this (non-inferiority)
DEFAULT_TAIL_TOL = 0.10        # cand tail over-prediction ratio <= base + this, per mask
DEFAULT_WORST_FOLD_TOL = 0.05  # worst year-fold delta must be >= -this
MIN_BETS = 200                 # per arm; below -> NO_DECISION (underpowered)
MIN_DAYS = 40                  # race-days per arm; below -> NO_DECISION


@dataclass(frozen=True)
class ArmPolicy:
    name: str
    n_bets: int
    n_bet_races: int
    recovery: float
    hit_rate: float
    winner_nll: float
    winner_races: int


@dataclass(frozen=True)
class EvWeightGateReport:
    cap: float
    threshold: float
    n_races: int
    base: ArmPolicy
    cand: ArmPolicy
    delta: float                 # recovery(cand) - recovery(base)
    ci_low: float | None
    ci_high: float | None
    b: int
    seed: int
    n_days: int
    by_fold: list[dict] = field(default_factory=list)   # {year, base, cand, delta}
    n_folds: int = 0
    n_folds_improved: int = 0
    worst_fold_delta: float = 0.0
    winner_nll_ok: bool = False
    tail_ok: bool = False
    tail: dict = field(default_factory=dict)             # per-mask base/cand over-prediction ratio
    verdict: str = "NO_DECISION"                         # ADOPT | REJECT | NO_DECISION
    reasons: dict = field(default_factory=dict)
    note: str = (
        "Closing-odds recovery is optimistic; only the relative paired delta is valid and ADOPT "
        "means prospective evaluation, never shipping (079). cap/threshold pre-registered."
    )


def _by_race(rows: list[dict]) -> dict[str, list[dict]]:
    races: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        races[r["race_id"]].append(r)
    return races


def _arm_bets_by_day(
    races: dict[str, list[dict]], *, threshold: float, cap: float
) -> tuple[dict, list, int, int]:
    """Return (bets_by_day, all_bets, n_bet_races, hits). Each bet = (odds, won)."""
    bets_by_day: dict = defaultdict(list)
    all_bets: list = []
    n_bet_races = 0
    hits = 0
    for rrows in races.values():
        bets = _bets_for_race(rrows, "ev_cap", threshold=threshold, cap=cap)
        if not bets:
            continue
        n_bet_races += 1
        day = rrows[0]["race_day"]
        for b in bets:
            bet = (float(b["odds"]), int(b["won"]))
            bets_by_day[day].append(bet)
            all_bets.append(bet)
            hits += bet[1]
    return bets_by_day, all_bets, n_bet_races, hits


def _recovery(bets: list) -> float:
    if not bets:
        return 0.0
    return sum(o for o, w in bets if w) / len(bets)


def _winner_nll(races: dict[str, list[dict]]) -> tuple[float, int]:
    """Race-level winner NLL from calibrated p of the actual winner (one sample per race)."""
    nlls = []
    for rrows in races.values():
        winner = next((r for r in rrows if r["won"]), None)
        if winner is None or winner["p"] is None:
            continue
        pw = min(max(float(winner["p"]), 1e-15), 1.0 - 1e-15)
        nlls.append(-math.log(pw))
    return (float(np.mean(nlls)) if nlls else float("nan"), len(nlls))


def _tail_over_prediction(rows: list[dict], *, cap: float) -> dict:
    """Over-prediction ratio E/O (predicted wins / observed wins) on tail masks.

    >1 means the model predicts more winners than occur (over-confidence). q = (1/odds)/Σ(1/odds)
    per race (market vote-share). Masks: odds>=cap and q<0.05 (047 longshot tail).
    """
    by_race = _by_race(rows)
    hi_e = hi_o = q_e = q_o = 0.0
    for rrows in by_race.values():
        inv = [1.0 / r["odds"] for r in rrows if r["odds"] and r["odds"] > 0]
        z = sum(inv) if inv else 0.0
        for r in rrows:
            if not r["odds"] or r["odds"] <= 0 or r["p"] is None:
                continue
            q = (1.0 / r["odds"]) / z if z > 0 else 0.0
            if r["odds"] >= cap:
                hi_e += float(r["p"])
                hi_o += float(r["won"])
            if q < 0.05:
                q_e += float(r["p"])
                q_o += float(r["won"])

    def ratio(e: float, o: float) -> float:
        return (e / o) if o > 0 else float("inf")

    return {
        "odds_ge_cap": {"E": hi_e, "O": hi_o, "ratio": ratio(hi_e, hi_o)},
        "q_lt_0_05": {"E": q_e, "O": q_o, "ratio": ratio(q_e, q_o)},
    }


def _paired_ratio_bootstrap(
    base_by_day: dict, cand_by_day: dict, *, b: int, seed: int
) -> tuple[float | None, float | None, int]:
    """Race-day cluster bootstrap of delta = recovery(cand) - recovery(base).

    Resamples DAYS with replacement and RECOMPUTES each arm's recovery ratio inside every
    replicate (codex #2 — not a mean of per-race diffs). Both arms share the same day draw
    (paired by day). <2 days => undefined CI.
    """
    days = sorted(set(base_by_day) | set(cand_by_day))
    n_days = len(days)
    if n_days < 2:
        return None, None, n_days
    base_arr = [base_by_day.get(d, []) for d in days]
    cand_arr = [cand_by_day.get(d, []) for d in days]
    rng = np.random.default_rng(seed)
    boots = np.empty(b, dtype=float)
    for i in range(b):
        pick = rng.integers(0, n_days, size=n_days)
        bbets: list = []
        cbets: list = []
        for j in pick:
            bbets.extend(base_arr[j])
            cbets.extend(cand_arr[j])
        boots[i] = _recovery(cbets) - _recovery(bbets)
    return (
        float(np.percentile(boots, 2.5)),
        float(np.percentile(boots, 97.5)),
        n_days,
    )


def evaluate_ev_weight_gate(
    base_rows: list[dict],
    cand_rows: list[dict],
    *,
    cap: float = DEFAULT_CAP,
    threshold: float = DEFAULT_THRESHOLD,
    seed: int = DEFAULT_SEED,
    b: int = DEFAULT_B,
    nll_tol: float = DEFAULT_NLL_TOL,
    tail_tol: float = DEFAULT_TAIL_TOL,
    worst_fold_tol: float = DEFAULT_WORST_FOLD_TOL,
    min_bets: int = MIN_BETS,
    min_days: int = MIN_DAYS,
) -> EvWeightGateReport:
    """Paired baseline↔candidate recovery gate with a ratio day-cluster bootstrap CI.

    Verdict (single rule, pre-registered 079 sec 3): ADOPT iff delta>0 AND ci_low>0 AND majority
    of year-folds improve AND worst-fold delta >= -worst_fold_tol AND both MUST guards pass.
    REJECT iff ci_high<=0 OR a MUST guard fails. NO_DECISION iff underpowered (< min_bets bets or
    < min_days race-days per arm) or the CI straddles 0.
    """
    base_races = _by_race(base_rows)
    cand_races = _by_race(cand_rows)

    base_by_day, base_bets, base_bet_races, base_hits = _arm_bets_by_day(
        base_races, threshold=threshold, cap=cap
    )
    cand_by_day, cand_bets, cand_bet_races, cand_hits = _arm_bets_by_day(
        cand_races, threshold=threshold, cap=cap
    )
    base_nll, base_wr = _winner_nll(base_races)
    cand_nll, cand_wr = _winner_nll(cand_races)

    base = ArmPolicy(
        "baseline", len(base_bets), base_bet_races, _recovery(base_bets),
        (base_hits / len(base_bets)) if base_bets else 0.0, base_nll, base_wr,
    )
    cand = ArmPolicy(
        "candidate", len(cand_bets), cand_bet_races, _recovery(cand_bets),
        (cand_hits / len(cand_bets)) if cand_bets else 0.0, cand_nll, cand_wr,
    )
    delta = cand.recovery - base.recovery
    ci_low, ci_high, n_days = _paired_ratio_bootstrap(base_by_day, cand_by_day, b=b, seed=seed)

    # per-fold (year) recovery
    def _fold_recovery(rows_by_race: dict, year: int) -> float:
        sub = {rid: rr for rid, rr in rows_by_race.items() if rr[0]["year"] == year}
        bets_by_day, allb, _, _ = _arm_bets_by_day(sub, threshold=threshold, cap=cap)
        return _recovery(allb)

    years = sorted({r["year"] for r in base_rows} | {r["year"] for r in cand_rows})
    by_fold = []
    for y in years:
        b_r = _fold_recovery(base_races, y)
        c_r = _fold_recovery(cand_races, y)
        by_fold.append({"year": y, "base": b_r, "cand": c_r, "delta": c_r - b_r})
    fold_deltas = [f["delta"] for f in by_fold]
    n_folds = len(by_fold)
    n_improved = sum(1 for d in fold_deltas if d > 0)
    worst = min(fold_deltas) if fold_deltas else 0.0

    # MUST guards
    winner_nll_ok = bool(math.isfinite(cand_nll) and cand_nll <= base_nll + nll_tol)
    base_tail = _tail_over_prediction(base_rows, cap=cap)
    cand_tail = _tail_over_prediction(cand_rows, cap=cap)
    tail_ok = True
    for mask in ("odds_ge_cap", "q_lt_0_05"):
        br = base_tail[mask]["ratio"]
        cr = cand_tail[mask]["ratio"]
        if math.isfinite(cr) and math.isfinite(br) and cr > br + tail_tol:
            tail_ok = False
    must_ok = winner_nll_ok and tail_ok

    underpowered = (
        base.n_bets < min_bets or cand.n_bets < min_bets or n_days < min_days
    )
    ci_straddles = ci_low is None or ci_high is None or (ci_low <= 0.0 <= ci_high)
    majority = 2 * n_improved > n_folds

    if underpowered:
        verdict = "NO_DECISION"
    elif not must_ok:
        verdict = "REJECT"
    elif ci_high is not None and ci_high <= 0.0:
        verdict = "REJECT"
    elif (delta > 0 and ci_low is not None and ci_low > 0.0 and majority
          and worst >= -worst_fold_tol):
        verdict = "ADOPT"
    elif ci_straddles:
        verdict = "NO_DECISION"
    else:
        verdict = "NO_DECISION"

    reasons = {
        "delta": delta,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "majority_folds": majority,
        "worst_fold_delta": worst,
        "winner_nll_ok": winner_nll_ok,
        "tail_ok": tail_ok,
        "underpowered": underpowered,
        "base_nll": base_nll,
        "cand_nll": cand_nll,
    }
    return EvWeightGateReport(
        cap=cap, threshold=threshold, n_races=len(set(base_races) | set(cand_races)),
        base=base, cand=cand, delta=delta, ci_low=ci_low, ci_high=ci_high, b=b, seed=seed,
        n_days=n_days, by_fold=by_fold, n_folds=n_folds, n_folds_improved=n_improved,
        worst_fold_delta=worst, winner_nll_ok=winner_nll_ok, tail_ok=tail_ok,
        tail={"base": base_tail, "cand": cand_tail}, verdict=verdict, reasons=reasons,
    )
