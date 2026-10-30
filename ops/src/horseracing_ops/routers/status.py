"""Service status (B5, 2026-10-27): is this ops process accepting writes right now?

Feature 172 pauses data collection OUTSIDE the FastAPI app: the launch wrapper sets
``HORSERACING_COLLECTION_MODE=paused`` and wraps the ASGI app so every non-GET request is answered
503 ``collection_paused`` before it reaches a router. The front/admin buttons therefore failed with
no visible reason. This endpoint lets them ask first.

Two facts are reported SEPARATELY because they can disagree:

* ``collection_paused`` — what THIS process enforces (the env var the wrapper sets). An ops process
  started without the wrapper does not reject writes, so it must not claim to.
* ``policy_file`` — what the repository's public policy (``config/source-operations.json``) says.
  When the two disagree in either direction (file paused / process accepting, or file active /
  process still rejecting = not restarted after the 2026-10-31 switch), ``policy_mismatch`` is
  true so an operator can see that a service bypassed the wrapper or is stale.

No database access, no worker liveness (nothing records a worker heartbeat; claiming one would
be a guess). GET only, so the wrapper lets it through.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from ..config import _REPO_ROOT, CONFIG
from ..schemas import OpsStatus, PolicyFileStatus

router = APIRouter(tags=["status"])

COLLECTION_MODE_ENV = "HORSERACING_COLLECTION_MODE"
POLICY_PATH_ENV = "OPS_SOURCE_OPERATIONS_PATH"
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"
PAUSED_DETAIL = "データ取得を停止中です。更新・予測・買い目生成・購入記録の受付を止めています。"
ACTIVE_DETAIL = "書き込みを受け付けています。"

_POLICY_KEYS = (
    "schema_version", "collection_paused", "source_interval_seconds",
    "ops_fetch_min_interval_seconds", "shared_limiter_enabled",
)


def read_policy_file(path: Path) -> PolicyFileStatus:
    """Report the public policy file as data; a missing or malformed file is a fact, not a 500."""
    if not path.is_file():
        return PolicyFileStatus(present=False)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return PolicyFileStatus(present=True, parse_error=type(exc).__name__)
    if not isinstance(raw, dict):
        return PolicyFileStatus(present=True, parse_error="not_an_object")
    fields = {}
    for key in _POLICY_KEYS:
        value = raw.get(key)
        # Only the declared type passes through; anything else becomes null rather than a guess
        # (bool is an int subclass, so numeric keys reject it explicitly).
        if key in ("collection_paused", "shared_limiter_enabled"):
            ok = isinstance(value, bool)
        elif key == "schema_version":
            ok = isinstance(value, str)
        else:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok:
            fields[key] = value
    return PolicyFileStatus(present=True, **fields)


def build_status(*, environ=os.environ, policy_path: Path | None = None,
                 fetch_min_interval: float | None = None) -> OpsStatus:
    mode = environ.get(COLLECTION_MODE_ENV)
    paused = (mode or "").strip().lower() == "paused"
    path = policy_path or Path(environ.get(POLICY_PATH_ENV) or DEFAULT_POLICY_PATH)
    policy = read_policy_file(path)
    interval = CONFIG.fetch_min_interval if fetch_min_interval is None else fetch_min_interval
    return OpsStatus(
        collection_paused=paused,
        writes_accepted=not paused,
        collection_mode=mode,
        policy_file=policy,
        # Either direction is a bypassed wrapper or a stale process: the file says paused but this
        # process accepts writes, or the file says active (2026-10-31) but this process still
        # rejects them (an ops-api not restarted after the policy changed).
        policy_mismatch=(policy.collection_paused is not None
                         and policy.collection_paused != paused),
        ops_fetch_min_interval_seconds=float(interval),
        detail=PAUSED_DETAIL if paused else ACTIVE_DETAIL,
    )


@router.get("/status", response_model=OpsStatus)
def get_status() -> OpsStatus:
    return build_status()
