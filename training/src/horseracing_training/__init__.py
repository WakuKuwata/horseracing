"""horseracing-training: win-model training, train-only calibration, adoption gate.

Implements the Feature 003 Predictor contract with a single win LightGBM whose
raw scores are calibrated on a train-internal held-out slice (no valid/test leak),
clipped, race-normalized and turned into top2/top3 via the shared Harville derivation.
"""

from __future__ import annotations

__all__ = ["LightGBMPredictor"]


def __getattr__(name: str):  # lazy to keep import cheap / avoid hard lightgbm import at pkg import
    if name == "LightGBMPredictor":
        from .predictor import LightGBMPredictor

        return LightGBMPredictor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
