"""US2: Platt calibration of overconfident scores improves win ECE."""

from __future__ import annotations

import numpy as np
from horseracing_eval.metrics import ece_label

from horseracing_training.calibration import fit_calibrator


def _miscalibrated_dataset():
    """Deterministic, overconfident scores.

    For each true frequency p in 0.1..0.9, emit 20 rows whose empirical win rate is exactly p,
    but whose model score is pushed away from 0.5 (slope > 1) — i.e. overconfident. Platt
    scaling should pull the slope back and reduce ECE.
    """
    raw: list[float] = []
    y: list[int] = []
    for tenth in range(1, 10):
        p = tenth / 10.0
        score = 0.5 + 1.6 * (p - 0.5)  # overconfident transform
        score = min(max(score, 1e-3), 1 - 1e-3)
        n = 20
        ones = round(p * n)
        for i in range(n):
            raw.append(score)
            y.append(1 if i < ones else 0)
    return np.array(raw), np.array(y)


def test_platt_calibration_reduces_ece():
    raw, y = _miscalibrated_dataset()
    ece_before = ece_label(raw, y, bins=10)

    cal = fit_calibrator(raw, y, method="platt")
    ece_after = ece_label(cal.transform(raw), y, bins=10)

    assert ece_after < ece_before
