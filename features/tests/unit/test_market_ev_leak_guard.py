"""Feature 137 (plan 0.7): the market-aware expected return never re-enters the model (憲法 II).

`market_ev_predictions` holds the output of a SEPARATE model that takes the current win odds as an
input. Its values exist for display only. If any feature builder, the main model's training
dataset/predictor/encoder/booster path, or the probability package could read them, current odds
would reach the main win model through the back door — exactly what constitution II forbids.

The guard is structural:

* no source file in ``features/src`` or ``probability/src`` names the table, its ORM class or the
  compute module;
* the main training feature path (``dataset`` / ``predictor`` / ``target_encoding`` /
  ``win_model``) and every module it transitively imports inside the training / features /
  probability packages neither names them nor imports the compute module (the import walk
  catches a relative ``from .market_ev import`` that a plain token scan would miss);
* no registry / materialized / model-input column carries the display's names.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
from horseracing_db.models import MarketEvPrediction

from horseracing_features.registry import REGISTRY, materialized_columns, model_input_features
from horseracing_features.schema import ALL_COLUMNS

_ROOT = Path(__file__).resolve().parents[3]
_FEATURES_SRC = _ROOT / "features" / "src" / "horseracing_features"
_PROBABILITY_SRC = _ROOT / "probability" / "src" / "horseracing_probability"
_TRAINING_SRC = _ROOT / "training" / "src" / "horseracing_training"
_PACKAGE_ROOTS = {
    "horseracing_features": _FEATURES_SRC,
    "horseracing_probability": _PROBABILITY_SRC,
    "horseracing_training": _TRAINING_SRC,
}
#: the main win model's feature/dataset path (plan 0.7)
_TRAINING_FEATURE_PATH = ("dataset", "predictor", "target_encoding", "win_model")
_COMPUTE_MODULE = "horseracing_training.market_ev"
_FORBIDDEN = ("market_ev_predictions", "MarketEvPrediction", _COMPUTE_MODULE)
#: display names that must never become feature column names
_FORBIDDEN_COLUMN_TOKENS = ("market_ev", "expected_return")


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def _token_offenders(paths: tuple[Path, ...]) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in paths:
        body = path.read_text(encoding="utf-8")
        hits = [token for token in _FORBIDDEN if token in body]
        if hits:
            offenders[str(path.relative_to(_ROOT))] = hits
    return offenders


def _source_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for package, root in _PACKAGE_ROOTS.items():
        for path in _python_files(root):
            relative = path.relative_to(root).with_suffix("")
            parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
            modules[".".join((package, *parts))] = path
    return modules


def _imported_names(path: Path, module_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = importlib.util.resolve_name("." * node.level + base, package)
            if base:
                imported.add(base)
            imported.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return imported


def _training_feature_path_closure() -> dict[str, Path]:
    """The feature-path modules plus everything they import inside the three packages."""
    modules = _source_modules()
    roots = [f"horseracing_training.{name}" for name in _TRAINING_FEATURE_PATH]
    missing = [name for name in roots if name not in modules]
    assert not missing, f"training feature path moved — update the guard: {missing}"

    visited: set[str] = set()
    pending = list(roots)
    while pending:
        module_name = pending.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        for imported in _imported_names(modules[module_name], module_name):
            candidate = imported
            while candidate and candidate not in modules:
                candidate = candidate.rpartition(".")[0]
            if candidate and candidate not in visited:
                pending.append(candidate)
    return {name: modules[name] for name in sorted(visited)}


def test_guard_tokens_name_the_real_table_and_class():
    # the guard would be vacuous if the ORM were renamed away from the tokens it scans for
    assert MarketEvPrediction.__name__ in _FORBIDDEN
    assert MarketEvPrediction.__tablename__ in _FORBIDDEN


def test_features_source_never_references_market_ev():
    paths = _python_files(_FEATURES_SRC)
    assert paths, "features source not found"
    offenders = _token_offenders(paths)
    assert not offenders, f"market-aware expected return leaked into features: {offenders}"


def test_probability_source_never_references_market_ev():
    paths = _python_files(_PROBABILITY_SRC)
    assert paths, "probability source not found"
    offenders = _token_offenders(paths)
    assert not offenders, f"market-aware expected return leaked into probability: {offenders}"


@pytest.mark.parametrize(
    "package_dir",
    [
        ("serving", "src", "horseracing_serving"),  # production predict / mixture correction
        ("betting", "src", "horseracing_betting"),  # buy-ups and their EV
        ("eval", "src", "horseracing_eval"),  # adoption / evaluation inputs
    ],
    ids=lambda parts: parts[0],
)
def test_other_model_consumers_never_reference_market_ev(package_dir):
    """SC-006: the market-aware values must not reach any production model input, so the packages
    that feed or evaluate the win model are scanned too (not only the feature path)."""
    paths = _python_files(_ROOT.joinpath(*package_dir))
    assert paths, f"{package_dir[0]} source not found"
    offenders = _token_offenders(paths)
    assert not offenders, f"market-aware expected return leaked into {package_dir[0]}: {offenders}"


def test_training_feature_path_never_references_market_ev():
    closure = _training_feature_path_closure()
    assert _COMPUTE_MODULE not in closure, (
        f"the main training feature path imports {_COMPUTE_MODULE}: {sorted(closure)}"
    )
    offenders = _token_offenders(tuple(closure.values()))
    assert not offenders, (
        f"market-aware expected return leaked into the training feature path: {offenders}"
    )


def test_no_market_ev_named_feature_columns():
    surfaces = {
        "registry": tuple(REGISTRY),
        "built matrix": ALL_COLUMNS,
        "materialized matrix": tuple(materialized_columns()),
        "model recipe": tuple(model_input_features()),
    }
    for surface, columns in surfaces.items():
        hits = [
            column
            for column in columns
            if any(token in column.lower() for token in _FORBIDDEN_COLUMN_TOKENS)
        ]
        assert not hits, f"market-aware expected return columns registered in {surface}: {hits}"
