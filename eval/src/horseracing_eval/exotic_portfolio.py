"""Combination-ticket portfolio backtest — the structure the documented JRA profits actually used.

Everything measured before this bought ONE ticket per race, in the WIN or PLACE pool, at a flat
stake, and topped out around 0.86. The two Supreme-Court-verified profitable Japanese operations
did none of those things: they bought 馬連 / 馬単 / 三連複, several hundred to over a thousand
combinations per race day, with stakes proportional to 1/odds, across essentially every race —
and returned 104.9% and 107.83% over 3 and 6 years.

Neither operation beat the market on win probability (public data, off-the-shelf software). What
they varied was WHICH COMBINATIONS to buy and HOW MUCH on each. That structure is what this
module scores.

Selection uses only the WIN pool (a market-q derived combination probability from the 009 engine);
settlement uses the REAL dividend. The exotic pool's own pre-race prices do not exist in the data,
so an `EV = P × O_real ≥ 1` rule is impossible here — the dividend is knowable only after the
race, and selecting on it would be choosing with the outcome in hand.

Pre-registration: docs/plan/prereg-exotic-portfolio.md (policies, K grid, multiplicity and the
demotion rules below were all fixed before the first run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .bootstrap import race_day_cluster_ratio_bootstrap_ci_v1

#: JRA payout rates by exotic bet type — the return a policy gets from a pool with no structure.
PAYOUT_RATE: dict[str, float] = {
    "wide": 0.775, "quinella": 0.775, "exacta": 0.75, "trio": 0.75, "trifecta": 0.725,
}

#: Pre-registered portfolio sizes. Fixed before the first run; adding one later is a new contract.
K_GRID: tuple[int, ...] = (1, 3, 5, 10, 20, 50)

#: Primary family (Holm-corrected). trifecta is diagnostic only — at ~1,409x average dividend a
#: single hit dominates the estimate, which was declared underpowered before running.
PRIMARY_BET_TYPES: tuple[str, ...] = ("wide", "quinella", "exacta", "trio")
DIAGNOSTIC_BET_TYPES: tuple[str, ...] = ("trifecta",)

#: A cell with too few hits, or whose payout is carried by one ticket, is NOT evidence however
#: tight its interval looks. Both thresholds were pre-registered.
MIN_HITS = 20
MAX_SINGLE_HIT_SHARE = 0.5


@dataclass(frozen=True)
class PortfolioRace:
    """One race: combination probabilities from the win pool, and what the exotic pool paid.

    ``probs`` maps a canonical combination key to its win-pool-derived probability; ``dividends``
    maps the SAME key shape to the real dividend for the combination(s) that came in (several on a
    dead heat). ``est_odds`` is the 010 estimated price, derived from win odds only, and is what
    the inverse-odds staking uses — using the real dividend to size a bet would be lookahead.
    """

    race_id: str
    day: str
    bet_type: str
    probs: dict[tuple[int, ...], float]
    est_odds: dict[tuple[int, ...], float]
    dividends: dict[tuple[int, ...], float]


def _top_k(race: PortfolioRace, k: int) -> list[tuple[int, ...]]:
    """The k most probable combinations. Ties broken by the combination itself so the portfolio
    is deterministic and independent of dict ordering."""
    return [c for c, _ in sorted(race.probs.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def _stakes(race: PortfolioRace, combos: list[tuple[int, ...]], staking: str) -> list[float]:
    if staking == "flat":
        return [1.0] * len(combos)
    if staking == "inverse_odds":
        # 1 unit per race spread so every ticket has the same expected payout at the estimated
        # price — the 大阪事件 structure (残高 × 係数 ÷ オッズ), computable pre-race from win odds.
        w = [1.0 / max(race.est_odds.get(c, 0.0), 1e-9) for c in combos]
        s = sum(w)
        return [x / s for x in w] if s > 0 else [0.0] * len(combos)
    raise ValueError(f"unknown staking: {staking}")


@dataclass(frozen=True)
class CellResult:
    bet_type: str
    k: int
    staking: str
    source: str
    n_races: int
    n_bets: int
    n_hits: int
    roi: float
    ci_low: float | None
    ci_high: float | None
    n_days: int
    max_single_hit_share: float
    leave_one_hit_out_roi: float
    p_value_one_sided: float          #: bootstrap P(ROI <= 1) — feeds the Holm screen
    verdict: str
    demoted_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return vars(self) | {}


def score_cell(
    races: list[PortfolioRace], *, k: int, staking: str, source: str,
    b: int, seed: int,
) -> CellResult:
    pay: dict[str, list[float]] = {}
    stake: dict[str, list[float]] = {}
    hits: list[float] = []
    n_bets = 0
    bet_type = races[0].bet_type
    for r in races:
        combos = _top_k(r, k)
        if not combos:
            continue
        for c, st in zip(combos, _stakes(r, combos, staking), strict=True):
            payout = st * r.dividends.get(c, 0.0)
            pay.setdefault(r.day, []).append(payout)
            stake.setdefault(r.day, []).append(st)
            n_bets += 1
            if payout > 0:
                hits.append(payout)
    if not pay:
        return CellResult(bet_type, k, staking, source, 0, 0, 0, float("nan"), None, None, 0,
                          float("nan"), float("nan"), 1.0, "NO_DECISION", "no bets")

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

    return CellResult(
        bet_type=bet_type, k=k, staking=staking, source=source,
        n_races=len(pay and races), n_bets=n_bets, n_hits=len(hits),
        roi=ci.point, ci_low=ci.ci_low, ci_high=ci.ci_high, n_days=ci.n_days,
        max_single_hit_share=max_share, leave_one_hit_out_roi=loho, p_value_one_sided=pval,
        verdict=verdict, demoted_reason=demoted,
    )


def holm_adjust(cells: list[CellResult], *, alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down over the pre-registered primary family (24 cells).

    The test per cell is the one-sided bootstrap p-value P(ROI <= 1). Holm sorts them ascending
    and requires p_(i) <= alpha / (m - i); the first failure stops the procedure and everything
    after it is rejected too. 24 cells were examined, so a family-wise claim must not rest on the
    luckiest one — an uncorrected 5% test would be expected to hand back ~1 false winner here.

    Demoted cells (too few hits, or one ticket carrying the payout) can never survive.
    """
    fam = [c for c in cells if c.staking == "flat" and c.source == "market"
           and c.bet_type in PRIMARY_BET_TYPES]
    out = {f"{c.bet_type}|k={c.k}": False for c in fam}
    m = len(fam)
    if m == 0:
        return out
    order = sorted(fam, key=lambda c: c.p_value_one_sided)
    for i, c in enumerate(order):
        if c.demoted_reason is not None or c.p_value_one_sided > alpha / (m - i):
            break          # Holm stops at the first failure
        out[f"{c.bet_type}|k={c.k}"] = True
    return out


def evaluate_portfolios(
    by_source: dict[str, dict[str, list[PortfolioRace]]], *, b: int = 2000, seed: int = 20260729,
) -> dict[str, Any]:
    """Score every (source, bet type, K, staking) cell.

    ``by_source`` maps a selection source ("market" = win-pool q, "model" = the OOF model p) to
    its per-bet-type races. Only the market/flat cells form the Holm-corrected primary family;
    the model arm was pre-registered as SECONDARY.
    """
    if not by_source:
        raise ValueError("no races")
    cells: list[CellResult] = []
    for source, by_bet_type in by_source.items():
        for races in by_bet_type.values():
            if not races:
                continue
            for k in K_GRID:
                for staking in ("flat", "inverse_odds"):
                    cells.append(score_cell(races, k=k, staking=staking, source=source,
                                            b=b, seed=seed))
    survives = holm_adjust(cells)
    return {
        "preregistration": "docs/plan/prereg-exotic-portfolio.md",
        "primary_family": "4 bet types x 6 K, flat staking, market-q selection (Holm screened)",
        "exploratory": "inverse_odds staking, trifecta — NOT corrected, not evidence alone",
        "reference_returns": PAYOUT_RATE,
        "demotion_rules": {"min_hits": MIN_HITS, "max_single_hit_share": MAX_SINGLE_HIT_SHARE},
        "cells": [c.to_dict() for c in cells],
        "holm_survivors": [k for k, v in survives.items() if v],
    }
