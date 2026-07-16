"""Feature 074 production-wiring and schema-zero boundary guards."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_PATHS = (
    _REPO_ROOT / "training/src/horseracing_training/legacy_attest.py",
    _REPO_ROOT / "training/src/horseracing_training/oof_generate.py",
    _REPO_ROOT / "training/src/horseracing_training/calib_manifest.py",
    _REPO_ROOT / "probability/src/horseracing_probability/oof_bundle.py",
)
_FORBIDDEN_IMPORT_PREFIXES = (
    "horseracing_serving",
    "horseracing_betting",
    "horseracing_api",
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


def test_074_modules_do_not_import_production_packages():
    for path in _SOURCE_PATHS:
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(_FORBIDDEN_IMPORT_PREFIXES)
        )
        assert not forbidden, (
            f"{path.relative_to(_REPO_ROOT)} imports production package(s): {forbidden}"
        )


def test_074_modules_do_not_declare_database_tables():
    for path in _SOURCE_PATHS:
        assert "__tablename__" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(_REPO_ROOT)} declares a database table"
        )
