"""T021: calibration leak boundary — p'/haircut/calibrator never reach the model side (017, SC-002).

The model side must not consume calibrated p', the edge haircut, or the Kelly stake — a *feature*
that read any of those would leak decision/market signal back into the model. Static scan of the
model-side sources.

Feature 074/076 reconciliation: 074 (merged, constitution-approved) deliberately moved OOF
calibration GENERATION into ``training/`` — model-OUTPUT calibration (``fit_power_gamma``/``_apply_gamma``
in ``calib_split.py``) and the 066 dispersion diagnostic calibrator (``cli.py``). Calibrating the
model's OUTPUT (or generating a diagnostic read-out) is NOT feeding calibrated values into a model
FEATURE, so ``training/`` may legitimately reference ``model_calibration``/``PCalibrator``. The tokens
that would signal a genuine leak — a feature or the booster consuming p'/stake/haircut/market/betting —
stay forbidden everywhere. ``features/`` (the pure feature layer) remains fully guarded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]

# Tokens whose presence anywhere on the model side means decision/market signal is leaking IN.
_LEAK_TOKENS = (
    "apply_p_calibrator", "calibration_eval", "stake_fraction",
    "haircut", "p_prime", "horseracing_betting",
)
# Additionally forbidden in the pure feature layer: even *referencing* the calibration machinery from
# a feature module is a boundary violation (features must be blind to calibration entirely).
_FEATURE_ONLY_TOKENS = ("model_calibration", "PCalibrator")

_FORBIDDEN = {
    "features": _LEAK_TOKENS + _FEATURE_ONLY_TOKENS,
    # training/ also gets the calibration-machinery tokens by default; only the specific 074
    # calibration-GENERATION modules below are exempted (codex: do not free training wholesale).
    "training": _LEAK_TOKENS + _FEATURE_ONLY_TOKENS,
}

#: The ONLY training modules allowed to touch the calibration machinery, and why. Anything else in
#: training/ referencing it is still a boundary violation — keeping the exemption file-scoped means a
#: new module cannot quietly start consuming calibrated values.
_CALIBRATION_GENERATION_FILES = {
    "training/src/horseracing_training/calib_split.py",  # 068/074 model-output calibration split
    "training/src/horseracing_training/cli.py",          # 066 dispersion diagnostic calibrator
}


def _sources(pkg: str):
    base = _ROOT / pkg / "src"
    return list(base.rglob("*.py")) if base.exists() else []


@pytest.mark.parametrize("pkg", ["features", "training"])
def test_model_side_free_of_calibration_and_haircut(pkg):
    offenders = []
    for f in _sources(pkg):
        rel = str(f.relative_to(_ROOT))
        exempt = rel in _CALIBRATION_GENERATION_FILES
        text = f.read_text(encoding="utf-8")
        for token in _FORBIDDEN[pkg]:
            if token not in text:
                continue
            if exempt and token in _FEATURE_ONLY_TOKENS:
                continue  # model-OUTPUT calibration generation (074) — not a feature-side leak
            offenders.append(f"{rel}: {token}")
    assert offenders == [], f"model-side leak (017/074): {offenders}"


def test_calibration_exemption_list_is_not_stale():
    """A stale exemption would silently widen the boundary — every entry must still exist."""
    for rel in _CALIBRATION_GENERATION_FILES:
        assert (_ROOT / rel).exists(), f"exempted file no longer exists: {rel}"
