"""Feature 078 US3 (T014): the promotion record — which generated manifest is ACTIVE in production.

Deliberately SEPARATE from the immutable, content-addressed manifests (codex D7): a manifest is
evidence and never changes, while "which candidate we chose to activate" is an operational decision
that changes over time. Keeping them apart means promoting/rolling back never rewrites an artifact.

The record is an append-only JSONL log (one line per promotion, newest last); rollback is a NEW line
pointing back at an earlier manifest_digest, never an edit. Timestamps are caller-supplied (an
operational log, so a real wall-clock time is passed in — never invented here, keeping the module
pure/testable). DB schema is unchanged; the log is a disk artifact next to the manifests.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_PROMOTIONS = "promotions.jsonl"


class PromotionError(ValueError):
    """The promotion log is malformed or the request is invalid."""


def _promotions_path(root: Path | str) -> Path:
    return Path(root) / "artifacts" / "oof" / _PROMOTIONS


def record_promotion(
    root: Path | str, *, manifest_digest: str, bundle_digest: str, at: str, note: str | None = None,
) -> dict:
    """Append a promotion entry (newest last) and return it. Never rewrites earlier entries.

    ``at`` is a caller-supplied ISO timestamp (operational log). Re-promoting the SAME digest
    that is already current is a no-op (idempotent) so a repeated activation does not bloat the log.
    """
    if not manifest_digest or not bundle_digest:
        raise PromotionError("manifest_digest and bundle_digest are required")
    entry = {"manifest_digest": manifest_digest, "bundle_digest": bundle_digest,
             "at": at, "note": note}
    current = current_promotion(root)
    if current is not None and current["manifest_digest"] == manifest_digest:
        return current  # idempotent: already the active manifest

    path = _promotions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    # append atomically: read-existing + rewrite via a temp file + rename (never a partial line).
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    fd, tmp = tempfile.mkstemp(prefix=f".{_PROMOTIONS}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(existing + line)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return entry


def read_promotions(root: Path | str) -> list[dict]:
    """All promotion entries in order (oldest first). Empty when nothing has been promoted."""
    path = _promotions_path(root)
    if not path.exists():
        return []
    out = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise PromotionError(f"corrupt promotion log at line {lineno}: {exc}") from exc
    return out


def current_promotion(root: Path | str) -> dict | None:
    """The currently-active manifest (the newest entry), or None if nothing is promoted."""
    entries = read_promotions(root)
    return entries[-1] if entries else None
