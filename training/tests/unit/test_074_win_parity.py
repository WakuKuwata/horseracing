"""Feature 074 static lgbm-063 serving-artifact byte-parity guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ORACLE_PATH = (
    _REPO_ROOT
    / "specs/073-eval-contract-correctness/legacy-freeze-lgbm-063.json"
)
_ARTIFACT_DIR = _REPO_ROOT / "artifacts/model_versions/lgbm-063"
_EXPECTED_ARTIFACTS = frozenset({"model.txt", "calibrator.pkl", "preprocessor.pkl"})


def _load_frozen_digests() -> dict[str, str]:
    oracle = json.loads(_ORACLE_PATH.read_text(encoding="utf-8"))
    return oracle["artifact_digests"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_lgbm_063_parity_oracle_contains_all_serving_artifacts():
    frozen_digests = _load_frozen_digests()
    assert _EXPECTED_ARTIFACTS <= frozen_digests.keys()


def test_lgbm_063_serving_artifacts_match_frozen_digests():
    frozen_digests = _load_frozen_digests()
    assert _EXPECTED_ARTIFACTS <= frozen_digests.keys()

    missing = sorted(
        filename for filename in _EXPECTED_ARTIFACTS if not (_ARTIFACT_DIR / filename).is_file()
    )
    if missing:
        pytest.skip(
            "lgbm-063 serving artifacts are absent; cannot compare frozen SHA-256 digests: "
            + ", ".join(missing)
        )

    for filename in sorted(_EXPECTED_ARTIFACTS):
        assert _sha256(_ARTIFACT_DIR / filename) == frozen_digests[filename]
