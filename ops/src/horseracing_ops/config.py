"""Tunable ops settings (Feature 024, T033) — env-overridable with safe defaults.

Centralises the freshness window (dedup), worker concurrency cap (netkeiba load, FR-016), stale
RUNNING recovery threshold, poll cadence, and fetch min-interval so operators can tune without code
changes. All values are read once at import; the worker/enqueue read from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class OpsConfig:
    fresh_seconds: int = _int("OPS_FRESH_SECONDS", 600)
    worker_concurrency: int = _int("OPS_WORKER_CONCURRENCY", 2)
    stale_running_seconds: int = _int("OPS_STALE_RUNNING_SECONDS", 900)
    poll_seconds: float = _float("OPS_POLL_SECONDS", 2.0)
    fetch_min_interval: float = _float("OPS_FETCH_MIN_INTERVAL", 1.0)


CONFIG = OpsConfig()
