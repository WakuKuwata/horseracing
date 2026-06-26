"""Single-bet Kelly fraction (Feature 016, research.md R1/R3).

For one bet at decimal odds O (payout multiple) with model hit-prob P_model:
  edge = P_model·O − 1            (== EV − 1)
  f*   = edge / (O − 1)           (standard Kelly: edge / net-odds)
  effective = clip(λ·f*, 0, cap_bet)
λ is λ_real for real odds, λ_est (more conservative) for estimated odds. A bet is REJECTED (returns
None) when O < o_min (denominator (O−1) blows up at low odds), edge ≤ min_edge (negative/zero edge —
skip, never bet), or estimated-odds bets when enable_estimated is False. The probability is ALWAYS
P_model (009 on model p); the market q is only inside O — p≠q (leak boundary).
"""

from __future__ import annotations

from .kelly_types import KellyConfig

_EPS = 1e-12


def single_kelly(
    p_model: float,
    odds_used: float,
    *,
    is_estimated: bool,
    cfg: KellyConfig,
) -> tuple[float, float, float] | None:
    """Return (edge, raw_fraction f*, effective_fraction) or None if the bet is filtered out.

    effective_fraction = clip(λ·f*, 0, cap_bet); allocation (kelly_allocation) later enforces the
    per-(race,bet_type) cap_total across bets.
    """
    if is_estimated and not cfg.enable_estimated:
        return None
    if odds_used < cfg.o_min:
        return None
    if odds_used <= 1.0 + _EPS:  # no upside — cannot bet
        return None
    edge = p_model * odds_used - 1.0
    if edge <= cfg.min_edge_for(is_estimated=is_estimated) + _EPS:
        return None
    raw = edge / (odds_used - 1.0)
    lam = cfg.lam(is_estimated=is_estimated)
    effective = min(max(lam * raw, 0.0), cfg.cap_bet)
    if effective <= 0.0:
        return None
    return edge, raw, effective
