"""Feature 073 US2 (FR-010, T021): freeze the current active model as the parity oracle.

The freeze is a CREATE-ONLY disk artifact (`freeze_073.json`) written next to the active
model's other artifacts. It records that the active model is ``calibration_split_unit=
race_count_v1`` (legacy race-count split) and pins the SHA-256 digests of its serving artifacts,
so SC-005 (serving prediction byte-invariance) has an immutable oracle.

Deliberately does NOT touch the DB ``model_versions`` row, the model/calibrator/preprocessor
bytes, or ``adoption_status`` (FR-011: this feature performs no active write/promotion). It is
append-only: writing the same content twice is idempotent; a differing content fails closed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FREEZE_FILENAME = "freeze_073.json"
FROZEN_SPLIT_UNIT = "race_count_v1"
_DIGEST_FILES = ("model.txt", "calibrator.pkl", "preprocessor.pkl")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_freeze_record(active_dir: Path | str, *, model_version: str, frozen_at: str) -> dict:
    """Compute the freeze record (digests of the serving artifacts) for an active model dir.

    ``frozen_at`` is passed in (callers stamp the time) to keep this deterministic/testable.
    """
    d = Path(active_dir)
    digests = {}
    for fn in _DIGEST_FILES:
        p = d / fn
        if not p.exists():
            raise FileNotFoundError(f"active artifact missing for freeze: {p}")
        digests[fn] = _sha256(p)
    return {
        "feature": "073-eval-contract-correctness",
        "model_version": model_version,
        "calibration_split_unit": FROZEN_SPLIT_UNIT,
        "artifact_digests": digests,
        "frozen_at": frozen_at,
        "note": (
            "Parity oracle for SC-005. This model predates Feature 073 and used the legacy "
            "race-count calibration split. Serving prediction bytes must be invariant across 073."
        ),
    }


def write_freeze_record(active_dir: Path | str, record: dict) -> Path:
    """Write ``freeze_073.json`` create-only (append-only, FR-010/FR-011).

    If the file already exists with identical content the write is idempotent; if it exists with
    DIFFERENT content the write fails closed (the oracle must never be silently mutated).
    """
    path = Path(active_dir) / FREEZE_FILENAME
    payload = json.dumps(record, indent=2, sort_keys=True)
    if path.exists():
        if path.read_text() == payload:
            return path  # idempotent
        raise ValueError(
            f"refusing to overwrite existing {FREEZE_FILENAME} with different content "
            f"(append-only, FR-010): {path}"
        )
    path.write_text(payload)
    return path
