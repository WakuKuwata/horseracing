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

# FL bias correction public API (Feature 013); imported after constants.
from .fl_bias import apply_calibrator, fit_fl_calibrator, load_samples  # noqa: E402
from .market_calibration import evaluate_q_vs_qprime  # noqa: E402

# Model p→p' calibration public API (Feature 017).
from .model_calibration import (  # noqa: E402
    PCalibrator,
    apply_p_calibrator,
    evaluate_calibration_db,
    evaluate_p_vs_pprime,
    fit_p_calibrator,
    load_p_samples,
)

__all__ = [
    "PROBABILITY_LOGIC_VERSION",
    "MARKET_LOGIC_VERSION",
    "fit_fl_calibrator",
    "apply_calibrator",
    "load_samples",
    "evaluate_q_vs_qprime",
    "PCalibrator",
    "fit_p_calibrator",
    "apply_p_calibrator",
    "evaluate_p_vs_pprime",
    "evaluate_calibration_db",
    "load_p_samples",
]
