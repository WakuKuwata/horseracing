"""Backward-compatible re-export shim (Feature 076).

The content-addressed OOF calibration manifest (schema/verify/build/write) was relocated to
``horseracing_probability.calib_manifest`` so the shared activation loader
(``probability/calib_activation.py``) can verify manifests without importing ``training`` — a cycle,
since ``training`` depends on ``probability`` and ``probability``'s env does not contain
``training``. The module has no training-specific dependency (only ``horseracing_eval.hashing``), so
the move is behaviour-preserving. ``training`` (build side) and existing 074 imports keep working
via this re-export (training -> probability is the allowed direction).
"""

from __future__ import annotations

from horseracing_probability.calib_manifest import (
    ARTIFACT_KIND,
    BASE_MODEL_VERSION,
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    ManifestConflict,
    ManifestError,
    build_manifest,
    verify_manifest,
    write_manifest,
)

__all__ = [
    "ARTIFACT_KIND",
    "BASE_MODEL_VERSION",
    "MANIFEST_FILENAME",
    "SCHEMA_VERSION",
    "ManifestConflict",
    "ManifestError",
    "build_manifest",
    "verify_manifest",
    "write_manifest",
]
