"""horseracing-betting: single-win EV recommendations + pseudo-ROI backtest.

Consumes Feature 006 predictions (win prob), computes EV = win_prob × settled odds, recommends
EV>=threshold as single-win bets (recommendations, append-only), and backtests pseudo-ROI vs
ROI baselines. ALL evaluation is **pseudo evaluation**: settled (result-time) odds are used as
both the EV input and the payout (closing-oracle simplification) — not realized ROI.
"""

from __future__ import annotations

BETTING_LOGIC_VERSION = "betting-0.1.0"

__all__ = ["BETTING_LOGIC_VERSION"]
