"""Bet the WIN pool using the EXOTIC pool's opinion.

Hausch–Ziemba–Rubinstein used what the win pool implied to bet the place pool. This runs the
arrow the other way: the exotic pool is the side measured to hold information the win pool lacks
(ΔR² = +0.001687, CI [+0.000736, +0.002736]), so its disagreements are bet in the win pool, which
carries the lowest takeout of any JRA market.

    x_i = Σ_{j≠i} devig(1/O_ij)   from the real 馬連 grid
    q_i = devig(1/win odds)
    bet horse i when x_i / q_i >= R

Every earlier selection rule here compared OUR estimate against a market, and the market was right
wherever they disagreed — so those rules were selecting our own error. Both sides of this ratio are
markets, so that particular confound cannot arise.

The REVERSE direction (q/x ≥ R) is scored alongside as a control. If the informational asymmetry is
real, the forward direction should beat it; if the two match, the selection is picking up
"horses the two pools disagree about" — a variance property — rather than information.

Pre-registration: docs/plan/prereg-cross-pool-win.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .bootstrap import race_day_cluster_ratio_bootstrap_ci_v1

#: 1 − takeout for the JRA win pool: what a policy returns with no edge.
WIN_PAYOUT_RATE = 0.80

#: Pre-registered ratio thresholds. Adding one later is a new contract.
THRESHOLDS: tuple[float, ...] = (1.05, 1.10, 1.20, 1.50)

MIN_HITS = 20
MAX_SINGLE_HIT_SHARE = 0.5


@dataclass(frozen=True)
class CrossPoolWinRace:
    """One race: both pools' views of every started horse, and who won.

    ``x`` and ``q`` are aligned with ``odds`` and each sum to 1 over the started field.
    """

    race_id: str
    day: str
    x: np.ndarray            #: exotic-pool marginal
    q: np.ndarray            #: win-pool vote share
    odds: np.ndarray         #: final win odds — the pari-mutuel settlement price
    winner_idx: int


@dataclass(frozen=True)
class Cell:
    direction: str
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
    mean_selected_odds: float
    verdict: str
    demoted_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return vars(self) | {}


def score_cell(
    races: list[CrossPoolWinRace], *, direction: str, threshold: float, b: int, seed: int,
) -> Cell:
    pay: dict[str, list[float]] = {}
    stake: dict[str, list[float]] = {}
    hits: list[float] = []
    sel_odds: list[float] = []
    n_bets = 0
    bet_races = 0
    for r in races:
        ratio = (r.x / r.q) if direction == "exotic_over_win" else (r.q / r.x)
        picked = False
        for i, ok in enumerate(ratio >= threshold):
            if not ok:
                continue
            picked = True
            payout = float(r.odds[i]) if i == r.winner_idx else 0.0
            pay.setdefault(r.day, []).append(payout)
            stake.setdefault(r.day, []).append(1.0)
            sel_odds.append(float(r.odds[i]))
            n_bets += 1
            if payout > 0:
                hits.append(payout)
        bet_races += int(picked)

    if not pay:
        return Cell(direction, threshold, 0, 0, 0, float("nan"), None, None, 0,
                    float("nan"), float("nan"), 1.0, float("nan"), "NO_DECISION", "no bets")

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
        direction=direction, threshold=threshold, n_races=bet_races, n_bets=n_bets,
        n_hits=len(hits), roi=ci.point, ci_low=ci.ci_low, ci_high=ci.ci_high, n_days=ci.n_days,
        max_single_hit_share=max_share, leave_one_hit_out_roi=loho, p_value_one_sided=pval,
        mean_selected_odds=float(np.mean(sel_odds)) if sel_odds else float("nan"),
        verdict=verdict, demoted_reason=demoted,
    )


def holm(cells: list[Cell], *, alpha: float = 0.05) -> dict[str, bool]:
    """Holm over the FORWARD family only; the reverse direction is a control, not a claim."""
    fam = [c for c in cells if c.direction == "exotic_over_win"]
    out = {f"T={c.threshold}": False for c in fam}
    m = len(fam)
    if m == 0:
        return out
    for i, c in enumerate(sorted(fam, key=lambda x: x.p_value_one_sided)):
        if c.demoted_reason is not None or c.p_value_one_sided > alpha / (m - i):
            break
        out[f"T={c.threshold}"] = True
    return out


def evaluate(
    races: list[CrossPoolWinRace], *, b: int = 2000, seed: int = 20260731,
) -> dict[str, Any]:
    if not races:
        raise ValueError("no races")
    cells = [score_cell(races, direction=d, threshold=t, b=b, seed=seed)
             for d in ("exotic_over_win", "win_over_exotic") for t in THRESHOLDS]
    # Blind reference: back every started horse. Lands on 1 − takeout when no selection is applied,
    # so a policy that cannot beat it is not selecting anything useful.
    blind = score_cell(races, direction="exotic_over_win", threshold=0.0, b=b, seed=seed)
    return {
        "preregistration": "docs/plan/prereg-cross-pool-win.md",
        "reference_return": WIN_PAYOUT_RATE,
        "blind_all_started": blind.to_dict(),
        "cells": [c.to_dict() for c in cells],
        "holm_survivors": [k for k, v in holm(cells).items() if v],
        "control_note": "win_over_exotic is the reverse-direction control: if the forward "
                        "direction does not beat it, the selection is picking disagreement "
                        "(variance), not information",
    }
