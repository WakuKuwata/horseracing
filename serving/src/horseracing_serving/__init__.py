"""horseracing-serving: inference-only pipeline over an adopted model.

Loads the active model + calibrator + preprocessor from artifacts, builds leak-safe as-of
features for target races (future / result-pending OK), runs raw->calibrate->clip->normalize
->Harville, and persists prediction_runs / race_predictions / feature_snapshots (append-only).
No schema change; no training.
"""

from __future__ import annotations

SERVING_LOGIC_VERSION = "serve-0.1.0"

__all__ = ["SERVING_LOGIC_VERSION"]
