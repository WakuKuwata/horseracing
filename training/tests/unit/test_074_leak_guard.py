"""Feature 074 static guards against calibration-to-feature leakage."""

from __future__ import annotations

import ast
from pathlib import Path

from horseracing_probability import oof_bundle

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DERIVATION_SOURCE_PATHS = (
    _REPO_ROOT / "training/src/horseracing_training/oof_generate.py",
    _REPO_ROOT / "training/src/horseracing_training/calib_manifest.py",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_oof_derivation_modules_do_not_import_features():
    for path in _DERIVATION_SOURCE_PATHS:
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith("horseracing_features")
        )
        assert not forbidden, (
            f"{path.relative_to(_REPO_ROOT)} imports feature engineering: {forbidden}"
        )


def test_oof_bundle_probability_fields_are_prediction_only():
    assert oof_bundle._PROBABILITY_FIELDS == {"win", "top2", "top3"}
