"""Content-addressed manifest for OOF calibration evidence (Feature 074 US4).

The manifest binds a legacy recipe attestation, an OOF prediction bundle, and the
calibration evaluation produced from that bundle.  It is deliberately disk-only:
publishing is create-only and verification fails closed before a consumer can use a
partial, tampered, unknown-generation artifact.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from horseracing_eval.hashing import stable_hash

SCHEMA_VERSION = 1
ARTIFACT_KIND = "oof_calibration"
BASE_MODEL_VERSION = "lgbm-063"
MANIFEST_FILENAME = "manifest.json"

_WALL_CLOCK_FIELDS = frozenset(
    {"created_at", "generated_at", "published_at", "written_at", "timestamp"}
)
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "base_model_version",
        "attestation_digest",
        "bundle_digest",
        "evaluation",
        "checksums",
        "probability_stage_order",
        "full_precision_params",
        "code_sha",
        "seed",
        "num_threads",
        "manifest_digest",
    }
)
_REQUIRED_CHECKSUMS = frozenset({"attestation", "bundle", "evaluation"})
_REQUIRED_TWO_GAMMA_PARAMS = frozenset({"gamma_lo", "gamma_hi", "pivot"})


class ManifestError(ValueError):
    """The manifest is partial, tampered, or belongs to an unsupported generation."""


class ManifestConflict(ManifestError):
    """A different manifest already occupies the bundle's create-only logical key."""


def _manifest_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical content covered by ``manifest_digest``.

    Only manifest-level wall-clock metadata is excluded.  Nested timestamps inside the
    evaluation remain evidence and therefore remain covered by the digest.
    """
    excluded = _WALL_CLOCK_FIELDS | {"manifest_digest"}
    return {key: value for key, value in manifest.items() if key not in excluded}


def _canonical_bytes(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"manifest is not canonically JSON serializable: {exc}") from exc
    return (payload + "\n").encode("utf-8")


def _require_fields(container: dict[str, Any], required: frozenset[str], *, scope: str) -> None:
    missing = sorted(required - container.keys())
    if missing:
        raise ManifestError(f"partial manifest: missing {scope} field(s): {', '.join(missing)}")


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _validate_full_precision_params(params: Any) -> None:
    if not isinstance(params, dict):
        raise ManifestError("full_precision_params must be a mapping")
    two_gamma = params.get("two_gamma")
    if not isinstance(two_gamma, dict):
        raise ManifestError("full_precision_params.two_gamma must be a mapping")
    _require_fields(two_gamma, _REQUIRED_TWO_GAMMA_PARAMS, scope="two_gamma")
    stage_lambdas = params.get("stage_lambdas")
    if not isinstance(stage_lambdas, dict) or not stage_lambdas:
        raise ManifestError("full_precision_params.stage_lambdas must be a non-empty mapping")


def build_manifest(
    *,
    attestation_digest: str,
    bundle_digest: str,
    evaluation: dict,
    stages: list[str],
    code_sha: str,
    seed: int,
    num_threads: int,
    two_gamma_lambda_full_precision: dict,
) -> dict:
    """Assemble a deterministic manifest without rounding calibration parameters."""
    evaluation_copy = copy.deepcopy(evaluation)
    params_copy = copy.deepcopy(two_gamma_lambda_full_precision)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "base_model_version": BASE_MODEL_VERSION,
        "attestation_digest": attestation_digest,
        "bundle_digest": bundle_digest,
        "evaluation": evaluation_copy,
        "checksums": {
            "attestation": attestation_digest,
            "bundle": bundle_digest,
            "evaluation": stable_hash(evaluation_copy),
        },
        "probability_stage_order": list(stages),
        "full_precision_params": params_copy,
        "code_sha": code_sha,
        "seed": seed,
        "num_threads": num_threads,
    }
    manifest["manifest_digest"] = stable_hash(_manifest_payload(manifest))
    verify_manifest(manifest)
    return manifest


def _load_manifest(path_or_dict: Path | str | dict) -> dict[str, Any]:
    if isinstance(path_or_dict, dict):
        return path_or_dict
    path = Path(path_or_dict)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ManifestError(f"manifest root must be an object: {path}")
    return loaded


def verify_manifest(path_or_dict: Path | str | dict) -> None:
    """Verify schema, generation, referenced checksums, and the manifest digest."""
    manifest = _load_manifest(path_or_dict)
    _require_fields(manifest, _REQUIRED_FIELDS, scope="top-level")

    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(f"unknown schema_version: {manifest['schema_version']!r}")
    if manifest["artifact_kind"] != ARTIFACT_KIND:
        raise ManifestError(f"unknown artifact_kind: {manifest['artifact_kind']!r}")
    if manifest["base_model_version"] != BASE_MODEL_VERSION:
        raise ManifestError(
            "generation mismatch: "
            f"expected {BASE_MODEL_VERSION!r}, got {manifest['base_model_version']!r}"
        )

    evaluation = manifest["evaluation"]
    checksums = manifest["checksums"]
    if not isinstance(evaluation, dict):
        raise ManifestError("evaluation must be a mapping")
    if not isinstance(checksums, dict):
        raise ManifestError("checksums must be a mapping")
    _require_fields(checksums, _REQUIRED_CHECKSUMS, scope="checksums")

    for name in ("attestation_digest", "bundle_digest", "manifest_digest"):
        if not _is_sha256(manifest[name]):
            raise ManifestError(f"invalid SHA-256 digest in {name}")
    for name in _REQUIRED_CHECKSUMS:
        if not _is_sha256(checksums[name]):
            raise ManifestError(f"invalid SHA-256 digest in checksums.{name}")

    expected_checksums = {
        "attestation": manifest["attestation_digest"],
        "bundle": manifest["bundle_digest"],
        "evaluation": stable_hash(evaluation),
    }
    for name, expected in expected_checksums.items():
        if checksums[name] != expected:
            raise ManifestError(
                f"checksum mismatch for {name}: expected {expected}, got {checksums[name]}"
            )

    for reference in ("attestation_digest", "bundle_digest", "base_model_version"):
        if reference in evaluation and evaluation[reference] != manifest[reference]:
            raise ManifestError(
                f"generation mismatch: evaluation.{reference} differs from manifest"
            )

    stage_order = manifest["probability_stage_order"]
    if not isinstance(stage_order, list) or not all(
        isinstance(stage, str) for stage in stage_order
    ):
        raise ManifestError("probability_stage_order must be a list of strings")
    _validate_full_precision_params(manifest["full_precision_params"])

    expected_digest = stable_hash(_manifest_payload(manifest))
    if manifest["manifest_digest"] != expected_digest:
        raise ManifestError(
            "manifest_digest mismatch: "
            f"expected {expected_digest}, got {manifest['manifest_digest']}"
        )


def _existing_canonical_bytes(path: Path) -> bytes:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestConflict(
            f"existing manifest is unreadable and cannot be replaced: {path}"
        ) from exc
    return _canonical_bytes(existing)


def write_manifest(root: Path | str, manifest: dict) -> Path:
    """Atomically publish a manifest under its bundle digest without replacing content.

    Writers serialize on the bundle directory while a same-filesystem rename promotes the
    completed temporary file. Identical canonical content is an idempotent success and
    different content raises ``ManifestConflict``.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be a mapping")
    bundle_digest = manifest.get("bundle_digest")
    if not _is_sha256(bundle_digest):
        raise ManifestError("invalid SHA-256 digest in bundle_digest")

    target = Path(root) / "artifacts" / "oof" / bundle_digest / MANIFEST_FILENAME
    canonical = _canonical_bytes(manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(target.parent, os.O_RDONLY)
    temporary: Path | None = None
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        if target.exists():
            if _existing_canonical_bytes(target) == canonical:
                verify_manifest(manifest)
                return target
            raise ManifestConflict(
                f"refusing to replace different create-only manifest: {target}"
            )

        verify_manifest(manifest)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{MANIFEST_FILENAME}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        os.fsync(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)
    return target
