"""horseracing-probability: joint probability engine (Plackett-Luce / Harville).

From per-race single-win probabilities derive all 7 JRA bet-type probabilities. Order is FIXED:
caller excludes scratched -> engine renormalizes Σ=1 -> clip[eps,1-eps] -> renormalize -> derive
(renormalize BEFORE PL denominators). wide{i,j}=Σ_k trio{i,j,k} (never an independent product).
Marginals agree with horseracing_eval.baselines.harville_topk. Derivation never reads
results/odds; exotic odds/EV/estimated-odds are out of scope (future P0).
"""

from __future__ import annotations

PROBABILITY_LOGIC_VERSION = "probability-0.1.0"
MARKET_LOGIC_VERSION = "market-0.1.0"

__all__ = ["PROBABILITY_LOGIC_VERSION", "MARKET_LOGIC_VERSION"]
