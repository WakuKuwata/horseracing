"""T016: leak boundary — Kelly stake / odds never become model inputs (Feature 016, SC-010).

The model side (features/training) must NOT depend on the betting side: a feature that read
stake_fraction, odds, or recommendations would leak market/decision data into the model. We scan the
horseracing_features and horseracing_training package sources for any such reference. (The betting →
features direction is fine: betting consumes model predictions.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN = ("horseracing_betting", "stake_fraction", "recommendations", "kelly")


def _sources(pkg: str):
    base = _ROOT / pkg / "src"
    return list(base.rglob("*.py")) if base.exists() else []


@pytest.mark.parametrize("pkg", ["features", "training"])
def test_model_side_does_not_reference_betting(pkg):
    offenders = []
    for f in _sources(pkg):
        text = f.read_text(encoding="utf-8")
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{f.relative_to(_ROOT)}: {token}")
    assert offenders == [], (
        f"model-side leak: {pkg} references betting/stake — odds/stake must never be a feature "
        f"({offenders})"
    )
