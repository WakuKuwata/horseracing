"""Keep the 129 builder's module globals per test.

``scripts/candidate_bundle_136.py`` rebinds seven globals of ``candidate_mixture_build`` at
import time (its builder drives 129's functions through those globals, by design). Pytest imports
each test module once per session, so after ``test_candidate_bundle_136`` has been collected the
129 tests would see the 136 members and fail only in a full run. Snapshot the originals here
(conftest is imported before any test module) and put them back for every test outside the 136
module; the 136 module gets the rebinding re-applied so its own assertions still hold. The
research source files are left untouched because their SHA is bound into frozen artifacts.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

try:
    import candidate_mixture_build as _B  # noqa: E402
except ImportError:  # e.g. the scrape venv (155/171 tests) has no lightgbm
    _B = None

_REBOUND = (
    "MEMBERS", "WORK", "make_recipe", "build_config", "build_source_paths", "source_hash",
    "upstream_state",
)
_ORIGINAL = {} if _B is None else {name: getattr(_B, name) for name in _REBOUND}

# Import the 136 driver here, once, so its import-time rebinding happens before any test module
# is collected, then put 129's globals back. Test modules that parametrize over ``MEMBERS`` at
# collection time therefore see 129's list; later ``importorskip`` calls return this cached module.
try:
    import candidate_bundle_136 as _C  # noqa: E402
except Exception:  # artifacts the driver needs may be absent on a fresh checkout
    _C = None
for _name in _ORIGINAL:
    setattr(_B, _name, _ORIGINAL[_name])


@pytest.fixture(autouse=True)
def _restore_129_builder_globals(request, monkeypatch):
    if _B is None:
        yield
        return
    if request.module.__name__.endswith("test_candidate_bundle_136"):
        if _C is not None:
            for name in _REBOUND:
                monkeypatch.setattr(_B, name, getattr(_C, name))
        yield
        return
    for name in _REBOUND:
        monkeypatch.setattr(_B, name, _ORIGINAL[name])
    yield
