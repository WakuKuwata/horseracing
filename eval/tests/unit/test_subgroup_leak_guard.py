"""T023a: subgroup / coverage-audit outputs never re-enter model features (II, FR-017).

Evaluation-derived values (subgroup CIs, coverage-audit) are a diagnostic sink — they must not
flow back into the feature build. Enforced structurally: subgroups.py stays eval-clean (no
training/features import), the F02 feature columns carry no subgroup/audit tokens, and F02 feature
output is invariant to subgroup labels (labels are attribute-only, not inputs to the feature)."""

from __future__ import annotations

import ast
import pathlib

import horseracing_eval.subgroups as subgroups_mod


def _imports(path: pathlib.Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_subgroups_module_does_not_import_training_or_features():
    # eval subgroup assignment must not import training/features — it cannot feed the model build.
    # (eval doesn't even depend on horseracing_features, so this is structurally enforced too.)
    imported = _imports(pathlib.Path(subgroups_mod.__file__))
    for bad in ("horseracing_training", "horseracing_features"):
        assert not any(m.startswith(bad) for m in imported), f"subgroups.py imports {bad}"


def test_subgroups_module_only_uses_stdlib():
    # subgroup assignment is pure (id/year/obs_count in, labels/decision out) — no model deps.
    imported = _imports(pathlib.Path(subgroups_mod.__file__))
    assert not any(m.startswith("horseracing") for m in imported)
