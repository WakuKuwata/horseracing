"""Feature 079: paired EV-weight adoption gate — PURE SCORER (self-contained).

Judges a market-aware EV-weighted candidate against the unweighted baseline on the SAME
walk-forward OOS race set. This is NOT the 064 policy_gate (which compares one model's uncapped
vs capped policy and has no CI) — codex #2: an ROI-oriented candidate needs an ACTIVE-vs-CANDIDATE
*paired* comparison with a cluster-bootstrap CI on the recovery difference.

Inputs = two per-horse row lists (baseline, candidate), each row a dict with keys
``race_id, horse_id, year, race_day, p (calibrated win prob), odds (closing or None), won (0/1)``.
Rows include EVERY started horse (unpriced horses carry ``odds=None``) so winner-NLL and tail
calibration see the complete field; the betting policy filters on odds. Both arms are scored under
the SAME fixed pre-registered policy (bet WIN where renorm-EV >= threshold and odds < cap; flat
stake), identical to betting.ev / policy_gate semantics.

PRIMARY estimand = paired recovery difference  delta = recovery(cand) - recovery(base),
recovery = sum(odds if won) / n_bets. Because the arms place DIFFERENT numbers of bets, recovery
is a RATIO of sums; the CI resamples whole RACE-DAYS and RECOMPUTES each arm's ratio inside every
replicate from per-day (payout, stake) sufficient statistics (codex #2 / #13) — NOT a
mean-of-per-race-diffs bootstrap. Replicates where either arm has zero stake are dropped (recorded).

MUST guards (fail => REJECT regardless of delta, codex H5): winner-NLL non-inferiority (improvement
NOT required) and tail-calibration non-degradation on the odds>=cap and q<0.05 masks (047: OOF p is
already ~3.5x over-confident in the longshot tail). Tail calibration uses calibration-in-the-large
(E-O)/N (always defined when N>0 — no fail-open on zero observed winners, codex B1); the O/E ratio
is reported as a diagnostic. Tail masks are baseline-defined (they use odds/q, identical for both
arms), never a candidate's own bets.

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
DEFAULT_TAIL_TOL = 0.02        # cand tail calibration-in-the-large <= base + this, per mask
DEFAULT_WORST_FOLD_TOL = 0.05  # worst year-fold delta must be >= -this
MIN_BETS = 200                 # per arm; below -> NO_DECISION (underpowered)
MIN_DAYS = 40                  # bet race-days PER ARM; below -> NO_DECISION


@dataclass(frozen=True)
class ArmPolicy:
    name: str
    n_bets: int
    n_bet_races: int
    n_bet_days: int
    recovery: float
    hit_rate: float
    net_per_bet: float           # recovery - 1
    coverage: float              # n_bet_races / n_races
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
    b_used: int                  # replicates with both arms' stake > 0
    seed: int
    n_days: int
    by_fold: list[dict] = field(default_factory=list)   # {year, base, cand, delta}
    n_folds: int = 0
    n_folds_improved: int = 0
    worst_fold_delta: float = 0.0
    winner_nll_ok: bool = False
    tail_ok: bool = False
    tail: dict = field(default_factory=dict)             # per-mask base/cand calib + O/E
    selection_jaccard: float = 0.0
    by_odds_band: list[dict] = field(default_factory=list)
    verdict: str = "NO_DECISION"                         # ADOPT | REJECT | NO_DECISION
    reasons: dict = field(default_factory=dict)
    #: pre-registered diagnostics intentionally NOT computed in this artifact-only run.
    deferred_diagnostics: tuple[str, ...] = (
        "top2_top3_noninferiority", "effective_sample_size", "leave_one_winner_out",
        "threshold_crossing_sensitivity", "tail_day_cluster_ci",
    )
    note: str = (
        "Closing-odds recovery is optimistic; only the relative paired delta is valid and ADOPT "
        "means prospective evaluation, never shipping (079). cap/threshold pre-registered."
    )


def _by_race(rows: list[dict]) -> dict[str, list[dict]]:
    races: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        races[r["race_id"]].append(r)
    return races


def _arm_stats(races: dict[str, list[dict]], *, threshold: float, cap: float):
    """Per-arm bet aggregation. Returns a dict of sufficient statistics + diagnostics."""
    day_payout: dict = defaultdict(float)
    day_stake: dict = defaultdict(int)
    n_bets = n_bet_races = hits = 0
    payout = 0.0
    selection: set[tuple] = set()
    band_pay: dict = defaultdict(float)
    band_n: dict = defaultdict(int)
    for rrows in races.values():
        bets = _bets_for_race(rrows, "ev_cap", threshold=threshold, cap=cap)
        if not bets:
            continue
        n_bet_races += 1
        day = rrows[0]["race_day"]
        for b in bets:
            o = float(b["odds"])
            n_bets += 1
            day_stake[day] += 1
            selection.add((b["race_id"], b.get("horse_id")))
            band_n[_band(o)] += 1
            if b["won"]:
                payout += o
                day_payout[day] += o
                band_pay[_band(o)] += o
                hits += 1
    return {
        "day_payout": day_payout, "day_stake": day_stake,
        "n_bets": n_bets, "n_bet_races": n_bet_races, "n_bet_days": len(day_stake),
        "hits": hits, "payout": payout, "selection": selection,
        "band_pay": band_pay, "band_n": band_n,
    }


_BANDS = (("<3", 0.0, 3.0), ("3-6", 3.0, 6.0), ("6-11", 6.0, 11.0), ("11-21", 11.0, 21.0))


def _band(o: float) -> str:
    for label, lo, hi in _BANDS:
        if lo <= o < hi:
            return label
    return "21+"


def _winner_nll(races: dict[str, list[dict]]) -> tuple[float, int]:
    """Race-level winner NLL. Excludes races without EXACTLY one winner (dead heat / none),
    matching the repo winner-NLL eligibility contract (codex M9)."""
    nlls = []
    for rrows in races.values():
        winners = [r for r in rrows if r["won"]]
        if len(winners) != 1 or winners[0]["p"] is None:
            continue
        pw = min(max(float(winners[0]["p"]), 1e-15), 1.0 - 1e-15)
        nlls.append(-math.log(pw))
    return (float(np.mean(nlls)) if nlls else float("nan"), len(nlls))


def _tail_metrics(rows: list[dict], *, cap: float) -> dict:
    """Per-mask tail calibration. Masks use ODDS/q only (identical for both arms -> baseline-
    defined). Complete-odds races only for q (060 complete-field). Returns, per mask:
    n, E=Σp, O=Σwon, calib_large=(E-O)/n (signed; >0 = over-prediction), oe_ratio=E/O (diagnostic).
    """
    by_race = _by_race(rows)
    hi_e = hi_o = hi_n = 0.0
    q_e = q_o = q_n = 0.0
    for rrows in by_race.values():
        odds = [r["odds"] for r in rrows]
        complete_odds = all(o is not None and o > 0 for o in odds)
        z = sum(1.0 / o for o in odds if o and o > 0)
        for r in rrows:
            o = r["odds"]
            if r["p"] is None:
                continue
            if o is not None and o >= cap:
                hi_e += float(r["p"])
                hi_o += float(r["won"])
                hi_n += 1
            if complete_odds and z > 0 and o and o > 0:
                q = (1.0 / o) / z
                if q < 0.05:
                    q_e += float(r["p"])
                    q_o += float(r["won"])
                    q_n += 1

    def pack(e, o, n):
        return {
            "n": int(n), "E": e, "O": o,
            "calib_large": ((e - o) / n) if n > 0 else None,
            "oe_ratio": (e / o) if o > 0 else (float("inf") if e > 0 else None),
        }
    return {"odds_ge_cap": pack(hi_e, hi_o, hi_n), "q_lt_0_05": pack(q_e, q_o, q_n)}


def _tail_guard(base_tail: dict, cand_tail: dict, *, tol: float) -> tuple[bool, dict]:
    """MUST guard: on each non-empty mask, cand calibration-in-the-large must not exceed base
    by more than tol. A mask with N==0 in both arms is not evaluable (no tail exposure) -> skip.
    If a mask is evaluable but a value is missing -> fail closed (codex B1: never fail open)."""
    ok = True
    detail: dict = {}
    for mask in ("odds_ge_cap", "q_lt_0_05"):
        bc = base_tail[mask]["calib_large"]
        cc = cand_tail[mask]["calib_large"]
        if base_tail[mask]["n"] == 0 and cand_tail[mask]["n"] == 0:
            detail[mask] = {"evaluable": False}
            continue
        if bc is None or cc is None:
            ok = False
            detail[mask] = {"evaluable": True, "pass": False, "reason": "undefined calibration"}
            continue
        passed = cc <= bc + tol
        detail[mask] = {"evaluable": True, "pass": passed, "base": bc, "cand": cc}
        ok = ok and passed
    return ok, detail


def _paired_ratio_bootstrap(base_stats, cand_stats, *, b: int, seed: int):
    """Race-day cluster bootstrap of delta from per-day (payout, stake) sufficient statistics.

    Resamples days with replacement (both arms share the draw — paired). Each replicate sums each
    arm's payout and stake over the picked days and forms recovery = Σpayout/Σstake; replicates
    where either arm has zero stake are DROPPED (undefined ratio, codex H6). O(days) per replicate.
    """
    days = sorted(set(base_stats["day_stake"]) | set(cand_stats["day_stake"]))
    n_days = len(days)
    if n_days < 2:
        return None, None, n_days, 0
    bp = np.array([base_stats["day_payout"].get(d, 0.0) for d in days])
    bs = np.array([base_stats["day_stake"].get(d, 0) for d in days], dtype=float)
    cp = np.array([cand_stats["day_payout"].get(d, 0.0) for d in days])
    cs = np.array([cand_stats["day_stake"].get(d, 0) for d in days], dtype=float)
    rng = np.random.default_rng(seed)
    deltas = np.empty(b, dtype=float)
    used = 0
    for _ in range(b):
        pick = rng.integers(0, n_days, size=n_days)
        bstake = bs[pick].sum()
        cstake = cs[pick].sum()
        if bstake <= 0 or cstake <= 0:
            continue
        deltas[used] = cp[pick].sum() / cstake - bp[pick].sum() / bstake
        used += 1
    if used < 2:
        return None, None, n_days, used
    d = deltas[:used]
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)), n_days, used


def _arm(name: str, stats: dict, n_races: int, nll: float, wr: int) -> ArmPolicy:
    rec = (stats["payout"] / stats["n_bets"]) if stats["n_bets"] else 0.0
    return ArmPolicy(
        name=name, n_bets=stats["n_bets"], n_bet_races=stats["n_bet_races"],
        n_bet_days=stats["n_bet_days"], recovery=rec,
        hit_rate=(stats["hits"] / stats["n_bets"]) if stats["n_bets"] else 0.0,
        net_per_bet=(rec - 1.0), coverage=(stats["n_bet_races"] / n_races) if n_races else 0.0,
        winner_nll=nll, winner_races=wr,
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
    validate_pairing: bool = True,
) -> EvWeightGateReport:
    """Paired baseline↔candidate recovery gate. See module docstring for the verdict rule."""
    # codex H7: fail closed if the arms are not the same (race,horse) population (a candidate must
    # never redefine the baseline population).
    if validate_pairing:
        bkey = {(r["race_id"], r.get("horse_id")) for r in base_rows}
        ckey = {(r["race_id"], r.get("horse_id")) for r in cand_rows}
        if bkey != ckey:
            raise ValueError(
                "paired gate: baseline and candidate row populations differ "
                f"(base only={len(bkey - ckey)}, cand only={len(ckey - bkey)}) — fail-closed"
            )

    base_races = _by_race(base_rows)
    cand_races = _by_race(cand_rows)
    n_races = len(set(base_races) | set(cand_races))

    bstats = _arm_stats(base_races, threshold=threshold, cap=cap)
    cstats = _arm_stats(cand_races, threshold=threshold, cap=cap)
    base_nll, base_wr = _winner_nll(base_races)
    cand_nll, cand_wr = _winner_nll(cand_races)
    base = _arm("baseline", bstats, n_races, base_nll, base_wr)
    cand = _arm("candidate", cstats, n_races, cand_nll, cand_wr)

    delta = cand.recovery - base.recovery
    ci_low, ci_high, n_days, b_used = _paired_ratio_bootstrap(bstats, cstats, b=b, seed=seed)

    # per-fold (year) recovery
    def _fold_rec(races_by_race: dict, year: int) -> float:
        sub = {rid: rr for rid, rr in races_by_race.items() if rr[0]["year"] == year}
        s = _arm_stats(sub, threshold=threshold, cap=cap)
        return (s["payout"] / s["n_bets"]) if s["n_bets"] else 0.0

    years = sorted({r["year"] for r in base_rows} | {r["year"] for r in cand_rows})
    by_fold = [
        {"year": y, "base": _fold_rec(base_races, y), "cand": _fold_rec(cand_races, y),
         "delta": _fold_rec(cand_races, y) - _fold_rec(base_races, y)}
        for y in years
    ]
    fold_deltas = [f["delta"] for f in by_fold]
    n_folds = len(by_fold)
    n_improved = sum(1 for d in fold_deltas if d > 0)
    worst = min(fold_deltas) if fold_deltas else 0.0

    # MUST guards
    winner_nll_ok = bool(math.isfinite(cand_nll) and cand_nll <= base_nll + nll_tol)
    base_tail = _tail_metrics(base_rows, cap=cap)
    cand_tail = _tail_metrics(cand_rows, cap=cap)
    tail_ok, tail_detail = _tail_guard(base_tail, cand_tail, tol=tail_tol)
    must_ok = winner_nll_ok and tail_ok

    # diagnostics
    inter = len(bstats["selection"] & cstats["selection"])
    union = len(bstats["selection"] | cstats["selection"])
    jaccard = (inter / union) if union else 0.0
    by_band = []
    for label, _lo, _hi in (*_BANDS, ("21+", 21.0, float("inf"))):
        n = cstats["band_n"].get(label, 0)
        if n:
            by_band.append({"band": label, "cand_n": n,
                            "cand_recovery": cstats["band_pay"].get(label, 0.0) / n})

    # verdict — codex H5: a MUST-guard failure REJECTS regardless of power; underpowered only
    # gates the (otherwise-passing) statistical decision.
    per_arm_days_ok = base.n_bet_days >= min_days and cand.n_bet_days >= min_days
    enough = base.n_bets >= min_bets and cand.n_bets >= min_bets and per_arm_days_ok
    ci_ok_pos = ci_low is not None and ci_low > 0.0
    ci_neg = ci_high is not None and ci_high <= 0.0
    majority = 2 * n_improved > n_folds

    if not must_ok:
        verdict = "REJECT"
    elif not enough:
        verdict = "NO_DECISION"
    elif ci_neg:
        verdict = "REJECT"
    elif delta > 0 and ci_ok_pos and majority and worst >= -worst_fold_tol:
        verdict = "ADOPT"
    else:
        verdict = "NO_DECISION"

    reasons = {
        "delta": delta, "ci_low": ci_low, "ci_high": ci_high, "b_used": b_used,
        "majority_folds": majority, "worst_fold_delta": worst,
        "winner_nll_ok": winner_nll_ok, "tail_ok": tail_ok, "tail_detail": tail_detail,
        "enough_power": enough, "base_nll": base_nll, "cand_nll": cand_nll,
        "base_bet_days": base.n_bet_days, "cand_bet_days": cand.n_bet_days,
    }
    return EvWeightGateReport(
        cap=cap, threshold=threshold, n_races=n_races, base=base, cand=cand, delta=delta,
        ci_low=ci_low, ci_high=ci_high, b=b, b_used=b_used, seed=seed, n_days=n_days,
        by_fold=by_fold, n_folds=n_folds, n_folds_improved=n_improved, worst_fold_delta=worst,
        winner_nll_ok=winner_nll_ok, tail_ok=tail_ok,
        tail={"base": base_tail, "cand": cand_tail, "guard": tail_detail},
        selection_jaccard=jaccard, by_odds_band=by_band, verdict=verdict, reasons=reasons,
    )
