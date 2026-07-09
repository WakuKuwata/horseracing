"""Feature 064: walk-forward betting-policy adoption gate — PURE SCORER (self-contained).

Given per-horse OOS rows (one row per started horse: race_id, year, model p, win odds, won), score
the CURRENT EV policy against the odds-capped policy on the SAME race set, plus honest baselines
(favorite / uniform / no-bet). Reports realized recovery, hit rate, skip rate, per-fold (year) and
per-odds-band breakdowns, and a relative adoption verdict.

This module imports NEITHER horseracing_betting NOR horseracing_training (betting already depends on
eval — importing it back would create a cycle; and eval stays predictor-agnostic). The win-only
selection predicates are re-implemented here on the raw rows (they are trivial); the semantics match
betting.ev.select_ev_bets: p is renormalized over the started population, EV = p_renorm × odds, a
capped horse stays in the denominator but is not bet.

IMPORTANT (constitution III): the cap value is a FIXED pre-registered input — never chosen from
these OOS results (that would be a selection leak). recovery uses CLOSING odds (the only odds
retained historically) so absolute recovery is optimistic (closing-oracle bias); only the RELATIVE
comparison (cap vs current, same population) is meaningful, and even the best policy stays < 1.0.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

#: odds bands for the diagnostic breakdown (upper-exclusive), matching the landscape study.
ODDS_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<3", 0.0, 3.0), ("3-6", 3.0, 6.0), ("6-11", 6.0, 11.0),
    ("11-21", 11.0, 21.0), ("21-51", 21.0, 51.0), ("51+", 51.0, float("inf")),
)

_CLOSING_ORACLE_NOTE = (
    "Recovery uses CLOSING odds (optimistic; purchase-time odds are not retained). Only the "
    "relative cap-vs-current comparison on the same population is valid. ROI>1 is NOT the bar; "
    "even the best policy stays <1.0. cap is pre-registered (no selection from these results)."
)


@dataclass(frozen=True)
class PolicyResult:
    name: str
    n_bets: int
    n_bet_races: int
    recovery: float          # Σ(odds if won else 0) / n_bets ; 0.0 when no bet (no_bet ⇒ ×1.0 ref)
    hit_rate: float
    skip_rate: float         # no-bet races / evaluated races


@dataclass(frozen=True)
class PolicyGateReport:
    threshold: float
    cap: float
    n_rows: int
    n_races: int
    policies: dict[str, PolicyResult]
    by_fold: list[dict] = field(default_factory=list)       # {year, ev, cap, delta}
    by_odds_band: list[dict] = field(default_factory=list)   # {band, n, ev_recovery}
    n_folds: int = 0
    n_folds_improved: int = 0
    worst_fold_delta: float = 0.0
    adopted: bool = False
    note: str = _CLOSING_ORACLE_NOTE


def _renorm(rows: list[dict]) -> dict[int, float]:
    """p renormalized over the started rows with p>0 (index → p'). Mirrors select_ev_bets."""
    tot = sum(r["p"] for r in rows if r["p"] is not None and r["p"] > 0.0)
    if tot <= 0.0:
        return {}
    return {i: (r["p"] / tot if r["p"] and r["p"] > 0.0 else 0.0) for i, r in enumerate(rows)}


def _bets_for_race(rows: list[dict], policy: str, *, threshold: float, cap: float) -> list[dict]:
    """Return the subset of started+priced rows this policy bets on (per-race)."""
    priced = [r for r in rows if r["odds"] is not None and r["odds"] > 0.0]
    if not priced:
        return []
    if policy == "favorite":
        return [min(priced, key=lambda r: r["odds"])]
    if policy == "uniform":
        return priced
    if policy == "no_bet":
        return []
    p = _renorm(rows)
    out = []
    for i, r in enumerate(rows):
        if r["odds"] is None or r["odds"] <= 0.0 or p.get(i, 0.0) <= 0.0:
            continue
        if policy == "ev_cap" and r["odds"] >= cap:      # 064: over-cap → no bet (in denom)
            continue
        if p[i] * r["odds"] >= threshold:
            out.append(r)
    return out


def _score(races: dict[str, list[dict]], policy: str, *, threshold: float, cap: float
           ) -> PolicyResult:
    n_bets = n_bet_races = hits = 0
    stake = payout = 0.0
    for rows in races.values():
        bets = _bets_for_race(rows, policy, threshold=threshold, cap=cap)
        if not bets:
            continue
        n_bet_races += 1
        for b in bets:
            n_bets += 1
            stake += 1.0
            if b["won"]:
                payout += b["odds"]
                hits += 1
    n_races = len(races)
    return PolicyResult(
        name=policy, n_bets=n_bets, n_bet_races=n_bet_races,
        recovery=(payout / stake) if stake > 0 else 0.0,
        hit_rate=(hits / n_bets) if n_bets > 0 else 0.0,
        skip_rate=((n_races - n_bet_races) / n_races) if n_races > 0 else 0.0,
    )


def _recovery_only(races: dict[str, list[dict]], policy: str, *, threshold: float, cap: float
                   ) -> float:
    return _score(races, policy, threshold=threshold, cap=cap).recovery


def evaluate_policy_gate(
    rows: list[dict], *, cap: float = 21.0, threshold: float = 1.0, worst_fold_tol: float = 0.01,
) -> PolicyGateReport:
    """Score current-EV vs odds-cap policy on the SAME OOS rows. rows: per-horse dicts with keys
    race_id, year, p (model win prob), odds (closing), won (0/1). cap/threshold are pre-registered.
    Adoption = cap recovery > current AND majority of folds improved AND worst-fold delta ≥ −tol."""
    races: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        races[r["race_id"]].append(r)

    cap_name = f"ev_oddscap{int(cap)}"
    policies = {
        "ev": _score(races, "ev", threshold=threshold, cap=cap),
        cap_name: _score(races, "ev_cap", threshold=threshold, cap=cap),
        "favorite": _score(races, "favorite", threshold=threshold, cap=cap),
        "uniform": _score(races, "uniform", threshold=threshold, cap=cap),
        "no_bet": _score(races, "no_bet", threshold=threshold, cap=cap),
    }

    # per-fold (year) recovery for current vs cap
    by_year: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for rid, rrows in races.items():
        y = rrows[0]["year"]
        by_year[y][rid] = rrows
    by_fold = []
    for y in sorted(by_year):
        ev_r = _recovery_only(by_year[y], "ev", threshold=threshold, cap=cap)
        cap_r = _recovery_only(by_year[y], "ev_cap", threshold=threshold, cap=cap)
        by_fold.append({"year": y, "ev": ev_r, "cap": cap_r, "delta": cap_r - ev_r})

    # per-odds-band recovery of the CURRENT ev bets (where the bleed is)
    ev_bets_all: list[dict] = []
    for rrows in races.values():
        ev_bets_all.extend(_bets_for_race(rrows, "ev", threshold=threshold, cap=cap))
    by_band = []
    for label, lo, hi in ODDS_BANDS:
        band = [b for b in ev_bets_all if lo <= b["odds"] < hi]
        if not band:
            continue
        pay = sum(b["odds"] for b in band if b["won"])
        by_band.append({"band": label, "n": len(band), "ev_recovery": pay / len(band)})

    deltas = [f["delta"] for f in by_fold]
    n_folds = len(by_fold)
    n_improved = sum(1 for d in deltas if d > 0)
    worst = min(deltas) if deltas else 0.0
    adopted = bool(
        policies[cap_name].recovery > policies["ev"].recovery
        and 2 * n_improved > n_folds
        and worst >= -worst_fold_tol
    )
    return PolicyGateReport(
        threshold=threshold, cap=cap, n_rows=len(rows), n_races=len(races), policies=policies,
        by_fold=by_fold, by_odds_band=by_band, n_folds=n_folds, n_folds_improved=n_improved,
        worst_fold_delta=worst, adopted=adopted,
    )
