"""Exotic ROI baselines (research.md R6, contracts/exotic_backtest.md).

Both baselines run through the SAME canonical field / selection / O_est path as the EV strategy, so
the comparison is on one population, stake, and K (FR-009). Success = EV strategy ROI beats each
baseline's ROI under identical conditions (NOT >1.0 absolute — everything is DOUBLE-pseudo).

- lowest_oest: each bet type's K lowest estimated-odds selections (the market's strongest picks),
  tie-broken by (o_est, selection_key) ascending — deterministic.
- uniform: K selections sampled by a deterministic seeded stride over selection_key order.
"""

from __future__ import annotations

from collections.abc import Iterable

from .exotic_ev import candidate_bets
from .exotic_selection import selection_key
from .exotic_types import ALL_EXOTIC, DEFAULT_SEED, CanonicalField, ExoticBet


def _k_for(top_k: int | dict[str, int], bet_type: str) -> int:
    if isinstance(top_k, dict):
        return int(top_k.get(bet_type, 0))
    return int(top_k)


def lowest_oest_baseline(
    field: CanonicalField,
    *,
    top_k: int | dict[str, int] = 5,
    bet_types: Iterable[str] = ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
) -> list[ExoticBet]:
    cands = candidate_bets(field, bet_types=bet_types, payout_rates=payout_rates, odds_cap=odds_cap)
    out: list[ExoticBet] = []
    for bt, bets in cands.items():
        k = _k_for(top_k, bt)
        if k <= 0:
            continue
        ordered = sorted(bets, key=lambda b: (b.o_est, selection_key(b.bet_type, b.selection)))
        out.extend(ordered[:k])
    return out


def uniform_baseline(
    field: CanonicalField,
    *,
    top_k: int | dict[str, int] = 5,
    bet_types: Iterable[str] = ALL_EXOTIC,
    seed: int = DEFAULT_SEED,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
) -> list[ExoticBet]:
    cands = candidate_bets(field, bet_types=bet_types, payout_rates=payout_rates, odds_cap=odds_cap)
    out: list[ExoticBet] = []
    for bt, bets in cands.items():
        k = _k_for(top_k, bt)
        if k <= 0:
            continue
        ordered = sorted(bets, key=lambda b: selection_key(b.bet_type, b.selection))
        n = len(ordered)
        if n <= k:
            out.extend(ordered)
            continue
        # deterministic seeded stride: evenly spaced picks offset by seed (no RNG)
        offset = seed % n
        step = n / k
        picks = sorted({int((offset + round(i * step)) % n) for i in range(k)})
        # fill if rounding collided (keep exactly min(k,n) distinct)
        idx = 0
        while len(picks) < k and idx < n:
            if idx not in picks:
                picks.append(idx)
            idx += 1
        out.extend(ordered[i] for i in sorted(picks)[:k])
    return out
