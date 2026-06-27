"""T021: calibration leak boundary — p'/haircut/calibrator never reach the model side (017, SC-002).

The features/training packages must not reference the calibration or haircut machinery: a feature
that consumed calibrated p', the edge haircut, or the Kelly stake would leak decision/market signal
back into the model. Static scan of the model-side sources.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN = (
    "model_calibration", "apply_p_calibrator", "PCalibrator", "calibration_eval",
    "stake_fraction", "haircut", "p_prime", "horseracing_betting",
)


def _sources(pkg: str):
    base = _ROOT / pkg / "src"
    return list(base.rglob("*.py")) if base.exists() else []


@pytest.mark.parametrize("pkg", ["features", "training"])
def test_model_side_free_of_calibration_and_haircut(pkg):
    offenders = []
    for f in _sources(pkg):
        text = f.read_text(encoding="utf-8")
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{f.relative_to(_ROOT)}: {token}")
    assert offenders == [], f"model-side leak (017): {offenders}"
