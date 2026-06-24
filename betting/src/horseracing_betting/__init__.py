"""horseracing-betting: single-win + exotic EV recommendations and pseudo-ROI backtest.

Single-win (007): EV = win_prob × settled odds. Exotic (011): EV = P_model(009 on model prob p) ×
O_est(010 on market odds q), computed on ONE canonical field (valid p AND valid odds) — p and q are
never mixed. Exotic recommendations and pseudo-ROI are DOUBLE-pseudo (estimated odds + PL
extrapolation): market_odds_used=null, is_estimated_odds=true. Bet selection never reads results.
"""

from __future__ import annotations

BETTING_LOGIC_VERSION = "betting-0.1.0"

# Exotic public API (imported after the constant to avoid a circular import).
from .exotic_backtest import run_exotic_backtest  # noqa: E402
from .exotic_ev import candidate_bets, canonical_field, exotic_ev_bets  # noqa: E402
from .exotic_recommend import generate_exotic_recommendations  # noqa: E402

__all__ = [
    "BETTING_LOGIC_VERSION",
    "canonical_field",
    "candidate_bets",
    "exotic_ev_bets",
    "generate_exotic_recommendations",
    "run_exotic_backtest",
]
