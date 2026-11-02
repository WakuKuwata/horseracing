"""T009/T010: leak + separation boundary (constitution II / VI).

ops/worker must not import the model/feature stack (so ingested odds/results can never become model
features) nor the read-only 014 API. Static import-graph check over the ops source tree.
"""

from __future__ import annotations

import ast
import pathlib

OPS_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "horseracing_ops"

FORBIDDEN = {
    "horseracing_training",   # II: ops must not touch the learning stack
    "horseracing_eval",
    "horseracing_features",   # II: ingested odds/results must not become features
    "horseracing_serving",
    "horseracing_betting",
    "horseracing_live",       # 053: range refresh runs the live CLI via subprocess, never imports it
    "horseracing_api",        # VI: write path must not import the read-only API
}


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return mods


def test_ops_has_no_forbidden_imports():
    offenders: dict[str, set[str]] = {}
    for py in OPS_SRC.rglob("*.py"):
        bad = _imports(py) & FORBIDDEN
        if bad:
            offenders[str(py.relative_to(OPS_SRC))] = bad
    assert not offenders, f"forbidden imports in ops (leak/separation boundary): {offenders}"


SCRAPE_SRC = pathlib.Path(__file__).resolve().parents[3] / "scrape" / "src" / "horseracing_scrape"


def test_scrape_never_imports_ops():
    """Feature 176 moved the capture-window computation from ops into scrape (ops re-exports it).
    The dependency stays one-way: scrape — and so every JRA route — never imports ops."""
    offenders = {
        str(py.relative_to(SCRAPE_SRC)): bad
        for py in SCRAPE_SRC.rglob("*.py")
        if (bad := _imports(py) & {"horseracing_ops"})
    }
    assert not offenders, f"scrape imports ops (reverse dependency): {offenders}"
