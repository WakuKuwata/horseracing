"""Content-addressed storage for out-of-fold prediction bundles.

The bundle is a disk-only research artifact.  Its identity is derived exclusively from the
explicit canonical payload below; database prediction runs and incidental metadata such as
wall-clock timestamps cannot affect it.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from horseracing_eval.hashing import race_set_hash, stable_hash

SCHEMA_VERSION = 1

_BUNDLE_FILENAME = "bundle.json"
_CANONICAL_FIELDS = (
    "schema_version",
    "predictions",
    "fold_boundaries",
    "per_fold",
    "oof_race_set_hash",
    "prediction_checksum",
    "attestation_digest",
)
_PER_FOLD_FIELDS = (
    "valid_year",
    "train_race_set_hash",
    "valid_race_set_hash",
    "train_through",
    "model_digest",
)
_PROBABILITY_FIELDS = frozenset(("win", "top2", "top3"))


class BundleError(RuntimeError):
    """Raised when an OOF bundle cannot be safely validated or published."""


def _normalise(value: Any) -> Any:
    """Return a JSON-safe value with deterministic mapping and set semantics."""
    if isinstance(value, Mapping):
        normalised: dict[str, Any] = {}
        for key, member in value.items():
            if not isinstance(key, str):
                raise BundleError(f"bundle mapping keys must be strings, got {type(key).__name__}")
            normalised[key] = _normalise(member)
        return normalised
    if isinstance(value, (set, frozenset)):
        members = [_normalise(member) for member in value]
        return sorted(members, key=stable_hash)
    if isinstance(value, (list, tuple)):
        return [_normalise(member) for member in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise BundleError("bundle values must not contain NaN or infinity")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise BundleError(f"unsupported bundle value type: {type(value).__name__}")


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in _CANONICAL_FIELDS if field not in payload]
    if missing:
        raise BundleError(f"bundle is missing canonical fields: {', '.join(missing)}")
    return {field: _normalise(payload[field]) for field in _CANONICAL_FIELDS}


def compute_bundle_digest(payload: dict) -> str:
    """Compute the bundle's deterministic content identity.

    ``bundle_digest`` itself and unknown/incidental top-level fields are intentionally excluded.
    Mapping order is handled by :func:`stable_hash`; sets are normalised recursively first because
    the shared helper otherwise stringifies them.
    """
    if not isinstance(payload, Mapping):
        raise BundleError("bundle payload must be a mapping")
    return stable_hash(_canonical_payload(payload))


def _validate_schema(payload: Mapping[str, Any]) -> None:
    if "schema_version" not in payload:
        raise BundleError("bundle is missing schema_version")
    version = payload["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise BundleError(
            f"schema_version mismatch: expected {SCHEMA_VERSION}, got {version!r}"
        )

    missing = [*(_CANONICAL_FIELDS), "bundle_digest"]
    missing = [field for field in missing if field not in payload]
    if missing:
        raise BundleError(f"bundle is missing required fields: {', '.join(missing)}")

    predictions = payload["predictions"]
    if not isinstance(predictions, Mapping):
        raise BundleError("predictions must be a mapping")
    for race_id, horses in predictions.items():
        if not isinstance(race_id, str) or not isinstance(horses, Mapping):
            raise BundleError("predictions must map string race IDs to horse mappings")
        for horse_id, probabilities in horses.items():
            if not isinstance(horse_id, str) or not isinstance(probabilities, Mapping):
                raise BundleError("each race must map string horse IDs to probability mappings")
            if set(probabilities) != _PROBABILITY_FIELDS:
                raise BundleError("each prediction must contain exactly win, top2, and top3")
            for stage, probability in probabilities.items():
                if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                    raise BundleError(f"prediction {stage} must be numeric")
                if not math.isfinite(float(probability)):
                    raise BundleError(f"prediction {stage} must be finite")

    boundaries = payload["fold_boundaries"]
    if not isinstance(boundaries, list) or any(
        type(valid_year) is not int for valid_year in boundaries
    ):
        raise BundleError("fold_boundaries must be a list of integer valid years")

    per_fold = payload["per_fold"]
    if not isinstance(per_fold, list):
        raise BundleError("per_fold must be a list")
    valid_years: list[int] = []
    for fold in per_fold:
        if not isinstance(fold, Mapping):
            raise BundleError("each per_fold entry must be a mapping")
        absent = [field for field in _PER_FOLD_FIELDS if field not in fold]
        if absent:
            raise BundleError(f"per_fold entry is missing fields: {', '.join(absent)}")
        if type(fold["valid_year"]) is not int:
            raise BundleError("per_fold valid_year must be an integer")
        valid_years.append(fold["valid_year"])
        for field in _PER_FOLD_FIELDS[1:]:
            if not isinstance(fold[field], str) or not fold[field]:
                raise BundleError(f"per_fold {field} must be a non-empty string")
    if valid_years != boundaries:
        raise BundleError("per_fold valid_year values must match fold_boundaries in order")

    for field in (
        "oof_race_set_hash",
        "prediction_checksum",
        "attestation_digest",
        "bundle_digest",
    ):
        if not isinstance(payload[field], str) or not payload[field]:
            raise BundleError(f"{field} must be a non-empty string")


def verify_bundle(payload: dict) -> None:
    """Fail closed unless the schema and all content-derived checksums are valid."""
    if not isinstance(payload, Mapping):
        raise BundleError("bundle payload must be a mapping")
    _validate_schema(payload)

    predictions = _normalise(payload["predictions"])
    expected_prediction_checksum = stable_hash(predictions)
    if payload["prediction_checksum"] != expected_prediction_checksum:
        raise BundleError(
            "prediction_checksum mismatch: bundle predictions have been modified"
        )

    expected_race_set_hash = race_set_hash(predictions)
    if payload["oof_race_set_hash"] != expected_race_set_hash:
        raise BundleError("oof_race_set_hash mismatch: bundle race membership has been modified")

    expected_digest = compute_bundle_digest(payload)
    if payload["bundle_digest"] != expected_digest:
        raise BundleError("bundle_digest mismatch: canonical bundle payload has been modified")


def build_payload(
    *,
    predictions: Mapping[str, Any],
    fold_boundaries: list[int],
    per_fold: list[Mapping[str, Any]],
    attestation_digest: str,
) -> dict:
    """Assemble a complete content payload with content-derived checksums (Feature 074 US1).

    Centralizes ``prediction_checksum`` / ``oof_race_set_hash`` so producers (``oof_generate``)
    cannot drift from :func:`verify_bundle`. ``bundle_digest`` is stamped later by
    :func:`write_bundle`. Predictions are normalised here so the checksums match verification.
    """
    normalised = _normalise(dict(predictions))
    return {
        "schema_version": SCHEMA_VERSION,
        "predictions": normalised,
        "fold_boundaries": list(fold_boundaries),
        "per_fold": [dict(fold) for fold in per_fold],
        "oof_race_set_hash": race_set_hash(normalised),
        "prediction_checksum": stable_hash(normalised),
        "attestation_digest": attestation_digest,
    }


def _stored_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stored = _canonical_payload(payload)
    digest = stable_hash(stored)
    supplied_digest = payload.get("bundle_digest")
    if supplied_digest is not None and supplied_digest != digest:
        raise BundleError("bundle_digest mismatch: refusing to publish inconsistent input")
    stored["bundle_digest"] = digest
    verify_bundle(stored)
    return stored


def _artifact_root(root: Path | str) -> Path:
    """Resolve a repository root, while also accepting the CLI's ``artifacts/oof`` path."""
    path = Path(root)
    if path.name == "oof" and path.parent.name == "artifacts":
        return path
    return path / "artifacts" / "oof"


def _existing_is_identical(path: Path, stored: Mapping[str, Any]) -> bool:
    existing = read_bundle(path)
    existing_stored = _stored_payload(existing)
    return existing_stored == stored


def write_bundle(root: Path | str, payload: dict) -> Path:
    """Atomically publish ``artifacts/oof/<digest>/bundle.json``.

    A valid identical artifact is an idempotent success.  Existing invalid or conflicting content
    is never overwritten.  The temporary file lives on the same filesystem and is always removed
    if publication fails.
    """
    if not isinstance(payload, Mapping):
        raise BundleError("bundle payload must be a mapping")
    stored = _stored_payload(payload)
    digest = stored["bundle_digest"]
    artifact_root = _artifact_root(root)
    artifact_dir = artifact_root / digest
    destination = artifact_dir / _BUNDLE_FILENAME

    if destination.exists():
        if _existing_is_identical(destination, stored):
            return destination
        raise BundleError(f"refusing to overwrite conflicting bundle: {destination}")

    try:
        artifact_root.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            dir=artifact_root,
            prefix=f".{digest}.",
            suffix=".tmp",
        )
    except OSError as exc:
        raise BundleError(f"could not prepare bundle publication under {artifact_root}") from exc

    temporary = Path(temporary_name)
    created_artifact_dir = False
    try:
        serialised = json.dumps(
            stored,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialised)
            handle.flush()
            os.fsync(handle.fileno())

        if destination.exists():
            if _existing_is_identical(destination, stored):
                return destination
            raise BundleError(f"refusing to overwrite conflicting bundle: {destination}")

        if not artifact_dir.exists():
            artifact_dir.mkdir()
            created_artifact_dir = True

        if destination.exists():
            if _existing_is_identical(destination, stored):
                return destination
            raise BundleError(f"refusing to overwrite conflicting bundle: {destination}")
        os.replace(temporary, destination)
        return destination
    except BundleError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise BundleError(f"failed to publish bundle at {destination}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if created_artifact_dir and not destination.exists():
            try:
                artifact_dir.rmdir()
            except OSError:
                pass


def read_bundle(path: Path | str) -> dict:
    """Read and verify a bundle JSON file (or a digest directory containing it)."""
    bundle_path = Path(path)
    if bundle_path.is_dir():
        bundle_path = bundle_path / _BUNDLE_FILENAME
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"could not read bundle: {bundle_path}") from exc
    if not isinstance(payload, dict):
        raise BundleError(f"bundle must contain a JSON object: {bundle_path}")
    verify_bundle(payload)
    return payload
