"""T018 (US4): static no-write boundary guard — AST + import graph (SC-005/SC-007, constitution II).

The API must be structurally incapable of writing: no betting/training imports anywhere in the
dependency closure, no Session write methods (commit/flush/add/...), and no write DML in SQL strings.
This complements the runtime DB-level READ ONLY transaction (deps.py).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "horseracing_api"

_FORBIDDEN_IMPORTS = ("horseracing_betting", "horseracing_training", "horseracing_ingest")
_WRITE_METHODS = {
    "commit", "flush", "add", "add_all", "delete", "merge",
    "bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings",
}
_WRITE_DML = ("insert ", "update ", "delete ", "truncate", "drop ", "alter ", "create ")


def _py_files():
    return list(SRC.rglob("*.py"))


def test_no_forbidden_imports_in_source():
    for f in _py_files():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith(_FORBIDDEN_IMPORTS), f"{f.name}: imports {a.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith(_FORBIDDEN_IMPORTS), f"{f.name}: from {mod}"


def test_no_session_write_method_calls():
    for f in _py_files():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in _WRITE_METHODS, f"{f.name}: calls .{node.func.attr}()"


def test_no_write_dml_in_sql_strings():
    for f in _py_files():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                assert not any(tok in low for tok in _WRITE_DML), f"{f.name}: write DML literal"


def test_import_graph_excludes_betting_and_training():
    # importing the app must not pull a write package into the process
    import horseracing_api.app  # noqa: F401
    assert "horseracing_betting" not in sys.modules
    assert "horseracing_training" not in sys.modules
