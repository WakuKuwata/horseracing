"""Hash contract + snapshot audit for paired evaluation (Feature 068, FR-018/V, codex C5/C9).

Six deterministic hashes disambiguate what must match across paired arms vs what may differ
(data-model.md §3, research D7 C5):

- ``feature_schema_hash`` / ``raw_matrix_content_hash`` — identical ACROSS A/B/C/D calib-split
  arms (same feature_version); for general cross-feature-version paired-eval (062 vs 061) they
  may differ and only race_id_set / fold / snapshot must match (analyze I3).
- ``model_race_set_hash`` / ``calib_race_set_hash`` — per-arm race partition (which races were
  model-fit vs calib-fit under that arm's split).
- ``transformed_matrix_hash`` / ``model_artifact_hash`` — per-arm; equal only on within-arm
  re-runs at ``num_threads=1`` (bit-parity is NOT guaranteed under multi-thread — I2).

Hashing is a stable SHA-256 over a canonical JSON encoding so it is reproducible across
processes (no Python ``hash()`` salting).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


def stable_hash(obj) -> str:
    """Deterministic SHA-256 hex of a JSON-canonical encoding of ``obj``.

    ``sort_keys`` + ``separators`` + ``default=str`` make the encoding stable across runs and
    tolerant of dates/Decimals. Not salted (unlike ``hash()``), so it is reproducible.
    """
    payload = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HashContract:
    feature_schema_hash: str
    raw_matrix_content_hash: str
    model_race_set_hash: str
    calib_race_set_hash: str
    transformed_matrix_hash: str
    model_artifact_hash: str

    def to_dict(self) -> dict:
        return asdict(self)


def race_set_hash(race_ids) -> str:
    """Order-independent hash of a race-id set (model-blind fixed set, FR-003/C8)."""
    return stable_hash(sorted(str(r) for r in race_ids))


@dataclass(frozen=True)
class SnapshotAudit:
    """Reproducibility envelope recorded on every paired report (V, codex C9)."""

    source_fingerprint: str | None
    manifest_hash: str | None
    result_entry_hash: str | None
    recipe_hash: str | None
    code_sha: str | None
    repeatable_read_snapshot: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
