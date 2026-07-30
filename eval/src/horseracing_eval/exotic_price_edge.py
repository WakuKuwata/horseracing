"""Buy the combinations the exotic pool prices below what the WIN pool implies.

This is the test every earlier combination measurement could not run. §5.2c selected with prices
SYNTHESISED from the win pool, and a synthesised price agrees with the win pool by construction —
the disagreement the whole idea rests on could not exist in that data. Hausch–Ziemba–Rubinstein
(1981) got positive expectation with no fundamental prediction at all, purely from what one pool
implies versus what another pool charges, and charging is the half we never had.

    EV = P_market(win pool -> 009 engine) x O_real(the exotic pool's own price)

Both sides are known before the race, so this selects on price rather than on outcome — unlike a
dividend-based rule, where the only priced combination is the one that came in.

The λ fit that preceded this was its prerequisite, not a detour: with the win-pool derivation
sitting 30% off the real grids, any observed gap was indistinguishable from our own arithmetic.

Pre-registration: docs/plan/prereg-exotic-real-price-edge.md (bet types, thresholds, judgement
rule, multiplicity and the demotion rules were all fixed before the first run).
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .bootstrap import race_day_cluster_ratio_bootstrap_ci_v1

#: 1 − takeout: what a policy returns from a pool it has no edge on.
PAYOUT_RATE: dict[str, float] = {"quinella": 0.775, "wide": 0.775, "trio": 0.750}

BET_TYPES: tuple[str, ...] = ("quinella", "wide", "trio")
#: Pre-registered EV thresholds. Adding one later is a new contract, not a tweak.
THRESHOLDS: tuple[float, ...] = (1.0, 1.1, 1.2, 1.5, 2.0)

#: A cell carried by one ticket, or with too few hits, is not evidence however tight its interval.
MIN_HITS = 20
MAX_SINGLE_HIT_SHARE = 0.5

#: Real-odds bands, reported so a "profitable" cell can be checked against confound 3: the win-pool
#: derivation is still 0.70–0.85 in the 1000x+ band, so a high EV there may be our error, not the
#: market's mispricing.
BANDS = [(0, 10, "~10"), (10, 50, "10-50"), (50, 200, "50-200"),
         (200, 1000, "200-1k"), (1000, math.inf, "1k+")]


@dataclass(frozen=True)
class PriceEdgeRace:
    """One race and bet type: what the win pool implies, what the pool charges, what it paid.

    ``prob`` and ``price`` are keyed identically (canonical selection tuples). ``dividends`` covers
    only the combination(s) that came in — absence IS the settlement.
    """

    race_id: str
    day: str
    bet_type: str
    prob: dict[tuple[int, ...], float]
    price: dict[tuple[int, ...], float]
    dividends: dict[tuple[int, ...], float]


@dataclass(frozen=True)
class Cell:
    bet_type: str
    threshold: float
    n_races: int
    n_bets: int
    n_hits: int
    roi: float
    ci_low: float | None
    ci_high: float | None
    n_days: int
    max_single_hit_share: float
    leave_one_hit_out_roi: float
    p_value_one_sided: float
    band_mix: dict[str, float]
    verdict: str
    demoted_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return vars(self) | {}


def _band_of(odds: float) -> str:
    for lo, hi, name in BANDS:
        if lo <= odds < hi:
            return name
    return BANDS[-1][2]


def score_cell(races: list[PriceEdgeRace], *, threshold: float, b: int, seed: int) -> Cell:
    pay: dict[str, list[float]] = {}
    stake: dict[str, list[float]] = {}
    hits: list[float] = []
    bands: collections.Counter = collections.Counter()
    n_bets = 0
    bet_races = 0
    bet_type = races[0].bet_type
    for r in races:
        picked = False
        for combo, p in r.prob.items():
            o = r.price.get(combo)
            if o is None or o <= 0 or p <= 0 or p * o < threshold:
                continue
            picked = True
            payout = r.dividends.get(combo, 0.0)
            pay.setdefault(r.day, []).append(payout)
            stake.setdefault(r.day, []).append(1.0)
            bands[_band_of(o)] += 1
            n_bets += 1
            if payout > 0:
                hits.append(payout)
        bet_races += int(picked)

    if not pay:
        return Cell(bet_type, threshold, 0, 0, 0, float("nan"), None, None, 0,
                    float("nan"), float("nan"), 1.0, {}, "NO_DECISION", "no bets")

    ci = race_day_cluster_ratio_bootstrap_ci_v1(pay, stake, b=b, seed=seed)
    total_pay = sum(sum(v) for v in pay.values())
    total_stake = sum(sum(v) for v in stake.values())
    max_share = (max(hits) / total_pay) if hits and total_pay > 0 else 0.0
    loho = ((total_pay - max(hits)) / total_stake) if hits and total_stake > 0 else float("nan")
    reps = np.asarray(ci.replicates, dtype=float)
    pval = float(np.mean(reps <= 1.0)) if reps.size else 1.0

    demoted = None
    if len(hits) < MIN_HITS:
        demoted = f"n_hits {len(hits)} < {MIN_HITS}"
    elif max_share > MAX_SINGLE_HIT_SHARE:
        demoted = f"one hit carries {max_share:.1%} of the payout"

    if demoted or ci.no_decision or ci.ci_low is None or ci.ci_high is None:
        verdict = "NO_DECISION"
    elif ci.ci_low > 1.0:
        verdict = "profitable"
    elif ci.ci_high < 1.0:
        verdict = "unprofitable"
    else:
        verdict = "NO_DECISION"

    return Cell(
        bet_type=bet_type, threshold=threshold, n_races=bet_races, n_bets=n_bets,
        n_hits=len(hits), roi=ci.point, ci_low=ci.ci_low, ci_high=ci.ci_high, n_days=ci.n_days,
        max_single_hit_share=max_share, leave_one_hit_out_roi=loho, p_value_one_sided=pval,
        band_mix={k: round(v / n_bets, 4) for k, v in sorted(bands.items())},
        verdict=verdict, demoted_reason=demoted,
    )


def holm(cells: list[Cell], *, alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down over the 15 pre-registered cells, on the one-sided bootstrap p-value.

    15 cells at an uncorrected 5% would be expected to hand back a false winner roughly once per
    run, so a family-wise claim cannot rest on the luckiest one. Demoted cells never survive.
    """
    out = {f"{c.bet_type}|T={c.threshold}": False for c in cells}
    m = len(cells)
    if m == 0:
        return out
    for i, c in enumerate(sorted(cells, key=lambda x: x.p_value_one_sided)):
        if c.demoted_reason is not None or c.p_value_one_sided > alpha / (m - i):
            break                      # Holm stops at the first failure
        out[f"{c.bet_type}|T={c.threshold}"] = True
    return out


def evaluate(
    by_bet_type: dict[str, list[PriceEdgeRace]], *, b: int = 2000, seed: int = 20260730,
) -> dict[str, Any]:
    if not any(by_bet_type.values()):
        raise ValueError("no races")
    cells = [score_cell(races, threshold=t, b=b, seed=seed)
             for bt in BET_TYPES if (races := by_bet_type.get(bt))
             for t in THRESHOLDS]
    survivors = holm(cells)
    return {
        "preregistration": "docs/plan/prereg-exotic-real-price-edge.md",
        "reference_returns": PAYOUT_RATE,
        "multiplicity": f"{len(cells)} cells, Holm-corrected",
        "demotion_rules": {"min_hits": MIN_HITS, "max_single_hit_share": MAX_SINGLE_HIT_SHARE},
        "cells": [c.to_dict() for c in cells],
        "holm_survivors": [k for k, v in survivors.items() if v],
    }
