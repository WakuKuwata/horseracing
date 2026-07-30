"""T056 (086): capture provenance stays outside learning and autonomous execution.

Capture metadata exists to audit how a chaos snapshot was obtained.  It must never become a
feature, calibration input, or bet-selection input.  The quarantine tables are recovery-only, and
capture-on-predict must not introduce a scheduler entry point.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
from pathlib import Path

from horseracing_features.registry import REGISTRY, materialized_columns, model_input_features
from horseracing_features.schema import ALL_COLUMNS

_ROOT = Path(__file__).resolve().parents[3]
_FEATURES_SRC = _ROOT / "features" / "src" / "horseracing_features"
_BETTING_SRC = _ROOT / "betting" / "src" / "horseracing_betting"
_CALIBRATION_PACKAGE_ROOTS = {
    "horseracing_probability": _ROOT / "probability" / "src" / "horseracing_probability",
    "horseracing_training": _ROOT / "training" / "src" / "horseracing_training",
}
_CAPTURE_PROVENANCE = (
    "capture_trigger",
    "capture_policy_version",
    "capture_strength",
    "seconds_to_post",
    "chaos_snapshot_id",
)
_QUARANTINE_TABLES = (
    "chaos_snapshots_quarantine",
    "chaos_readouts_quarantine",
)


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def _token_offenders(
    paths: tuple[Path, ...],
    tokens: tuple[str, ...],
) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        hits = sorted(token for token in tokens if token.lower() in text)
        if hits:
            offenders[str(path.relative_to(_ROOT))] = hits
    return offenders


def _imported_names(path: Path, module_name: str | None = None) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    package = None
    if module_name is not None:
        package = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and package:
                relative_name = "." * node.level + base
                base = importlib.util.resolve_name(relative_name, package)
            if base:
                imported.add(base)
            imported.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return imported


def _source_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for package, root in _CALIBRATION_PACKAGE_ROOTS.items():
        for path in _python_files(root):
            relative = path.relative_to(root).with_suffix("")
            parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
            module_name = ".".join((package, *parts))
            modules[module_name] = path
    return modules


def _calibration_import_closure() -> tuple[Path, ...]:
    """Find calibration inputs without maintaining a hand-written module list."""

    modules = _source_modules()
    pending = [name for name in modules if "calib" in name.rpartition(".")[2].lower()]
    assert pending, "no calibration modules discovered"
    visited: set[str] = set()

    while pending:
        module_name = pending.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        for imported in _imported_names(modules[module_name], module_name):
            candidate = imported
            while candidate and candidate not in modules:
                candidate = candidate.rpartition(".")[0]
            if candidate in modules and candidate not in visited:
                pending.append(candidate)

    return tuple(sorted(modules[name] for name in visited))


def _chaos_import_offenders(paths: tuple[Path, ...]) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in paths:
        bad = sorted(name for name in _imported_names(path) if "chaos" in name.lower())
        if bad:
            offenders[str(path.relative_to(_ROOT))] = bad
    return offenders


def test_capture_provenance_not_in_registry_or_feature_matrix_columns():
    feature_surfaces = {
        "registry": tuple(REGISTRY),
        "built matrix": ALL_COLUMNS,
        "materialized matrix": tuple(materialized_columns()),
        "model recipe": tuple(model_input_features()),
    }
    for surface, columns in feature_surfaces.items():
        lowered = tuple(column.lower() for column in columns)
        for token in _CAPTURE_PROVENANCE:
            assert not any(token in column for column in lowered), (
                f"capture provenance '{token}' leaked into {surface}"
            )


def test_features_package_never_references_capture_provenance():
    offenders = _token_offenders(_python_files(_FEATURES_SRC), _CAPTURE_PROVENANCE)
    assert not offenders, f"capture provenance leaked into the features package: {offenders}"


def test_model_consumers_do_not_import_chaos_modules():
    consumer_paths = (
        *_python_files(_FEATURES_SRC),
        *_calibration_import_closure(),
        *_python_files(_BETTING_SRC),
    )
    offenders = _chaos_import_offenders(consumer_paths)
    assert not offenders, f"model/calibration/betting import graph reaches chaos code: {offenders}"


def test_capture_provenance_not_in_calibration_inputs_or_bet_selection():
    calibration_offenders = _token_offenders(
        _calibration_import_closure(),
        _CAPTURE_PROVENANCE,
    )
    betting_offenders = _token_offenders(_python_files(_BETTING_SRC), _CAPTURE_PROVENANCE)
    assert not calibration_offenders, (
        f"capture provenance leaked into calibration inputs: {calibration_offenders}"
    )
    assert not betting_offenders, (
        f"capture provenance leaked into bet selection: {betting_offenders}"
    )


def test_migration_head_is_0015_exotic_quotes():
    versions = sorted(
        path.stem
        for path in (_ROOT / "db" / "migrations" / "versions").glob("0*.py")
    )
    assert versions[-1] == "0015_exotic_quotes", versions[-1]


def test_capture_policy_version_is_not_a_prospective_stratification_key():
    path = _ROOT / "training" / "src" / "horseracing_training" / "chaos_bands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    by_keys = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("by_")
    }
    assert "by_capture_trigger" in by_keys, "expected capture-trigger stratification guard"
    assert not any("capture_policy_version" in key for key in by_keys), sorted(by_keys)


def test_quarantine_tables_have_no_readers_in_api_training_or_features():
    consumer_roots = (
        _ROOT / "api" / "src",
        _ROOT / "training" / "src",
        _ROOT / "features" / "src",
    )
    consumer_paths = tuple(
        path
        for root in consumer_roots
        for path in sorted(root.rglob("*.py"))
    )
    offenders = _token_offenders(consumer_paths, _QUARANTINE_TABLES)
    assert not offenders, f"recovery-only quarantine table gained a reader: {offenders}"


_SCHEDULER_PATTERNS = {
    "APScheduler": re.compile(r"\bapscheduler\b", re.IGNORECASE),
    "APScheduler registration": re.compile(
        r"\b(?:BackgroundScheduler|BlockingScheduler|AsyncIOScheduler)\s*\(|\.add_job\s*\("
    ),
    "Celery beat": re.compile(
        r"\b(?:beat_schedule|add_periodic_task|periodic_task)\b"
        r"|\bcelery\s+beat\b|\bcelery\b[^\n]*\s-B(?:\s|$)",
        re.IGNORECASE,
    ),
    "cron registration": re.compile(
        r"(?im)^\s*(?:-\s*)?cron\s*:|\bcrontab\b|/(?:etc/)?cron\.d\b|node-cron"
    ),
    "systemd timer": re.compile(
        r"(?im)^\s*\[Timer\]\s*$|^\s*On(?:Calendar|UnitActiveSec|BootSec)\s*="
    ),
}
_SCHEDULER_SCAN_SUFFIXES = {
    ".cfg",
    ".conf",
    ".ini",
    ".js",
    ".py",
    ".service",
    ".sh",
    ".timer",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_SCHEDULER_SCAN_FILENAMES = {"Dockerfile", "Makefile", "crontab", "package.json"}
_SCHEDULER_SCAN_EXCLUDED_DIRS = {
    ".agents",
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".specify",
    ".venv",
    "artifacts",
    "docs",
    "memory",
    "node_modules",
    "out",
    "specs",
    "tests",
}


def _production_entrypoint_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in _SCHEDULER_SCAN_EXCLUDED_DIRS
        ]
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if (
                path.suffix in _SCHEDULER_SCAN_SUFFIXES
                or filename in _SCHEDULER_SCAN_FILENAMES
            ):
                files.append(path)
    return tuple(sorted(files))


def test_feature086_adds_no_scheduler_entrypoint():
    offenders: dict[str, list[str]] = {}
    for path in _production_entrypoint_files():
        hits: list[str] = []
        if path.suffix == ".timer" or path.name.lower() == "crontab":
            hits.append("scheduler entrypoint filename")
        text = path.read_text(encoding="utf-8")
        hits.extend(name for name, pattern in _SCHEDULER_PATTERNS.items() if pattern.search(text))
        if hits:
            offenders[str(path.relative_to(_ROOT))] = sorted(set(hits))
    assert not offenders, f"operator-triggered capture gained a scheduler entrypoint: {offenders}"
