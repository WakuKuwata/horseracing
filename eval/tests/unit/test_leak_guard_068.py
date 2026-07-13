"""T012 (subset): eval→training import boundary + hash-contract determinism (FR-016/FR-018)."""

from __future__ import annotations

import ast
import pathlib

import horseracing_eval.bootstrap as bootstrap_mod
import horseracing_eval.foldfit as foldfit_mod
import horseracing_eval.hashing as hashing_mod
import horseracing_eval.metrics as metrics_mod
from horseracing_eval.hashing import HashContract, race_set_hash, stable_hash

_EVAL_SRC = pathlib.Path(metrics_mod.__file__).parent


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_eval_new_modules_do_not_import_training():
    # 020 boundary: eval is predictor-agnostic; the new 068 modules must not import training.
    for mod in (metrics_mod, bootstrap_mod, hashing_mod, foldfit_mod):
        path = pathlib.Path(mod.__file__)
        for imported in _imported_modules(path):
            assert not imported.startswith("horseracing_training"), (
                f"{path.name} imports {imported} — eval→training boundary violated"
            )


def test_no_eval_file_imports_training():
    for py in _EVAL_SRC.glob("*.py"):
        for imported in _imported_modules(py):
            assert not imported.startswith("horseracing_training"), f"{py.name} imports {imported}"


def test_stable_hash_is_reproducible_and_order_independent_for_sets():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
    assert race_set_hash(["r3", "r1", "r2"]) == race_set_hash(["r1", "r2", "r3"])
    # different set -> different hash
    assert race_set_hash(["r1", "r2"]) != race_set_hash(["r1", "r2", "r3"])


def test_hash_contract_serializes_six_hashes():
    hc = HashContract("a", "b", "c", "d", "e", "f")
    d = hc.to_dict()
    assert set(d.keys()) == {
        "feature_schema_hash", "raw_matrix_content_hash",
        "model_race_set_hash", "calib_race_set_hash",
        "transformed_matrix_hash", "model_artifact_hash",
    }
