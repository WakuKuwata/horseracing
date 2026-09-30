"""Tunable ops settings (Feature 024, T033) — env-overridable with safe defaults.

Centralises the freshness window (dedup), worker concurrency cap (netkeiba load, FR-016), stale
RUNNING recovery threshold, poll cadence, and fetch min-interval so operators can tune without code
changes. All values are read once at import; the worker/enqueue read from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Feature 137: the market-aware 期待回収率 model directory (gitignored artifact). Absolute and
#: derived from this file's location (ops/src/horseracing_ops/config.py → repo root = parents[3]),
#: because the training CLI rejects relative paths and anything under `.claude/worktrees/`.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MARKET_EV_MODEL_DIR = _REPO_ROOT / "artifacts" / "market_ev" / "mev-binary-v2"

_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in _FALSE_WORDS


def _str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(x.strip() for x in raw.split(",") if x.strip())


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
    #: Threads for the CPU lane (predict/recommend subprocesses — no netkeiba traffic, so this is
    #: bounded by memory, not politeness: a predict subprocess peaks ~3.4GB RSS, so 3 ≈ 10GB).
    cpu_concurrency: int = _int("OPS_CPU_CONCURRENCY", 3)
    stale_running_seconds: int = _int("OPS_STALE_RUNNING_SECONDS", 900)
    poll_seconds: float = _float("OPS_POLL_SECONDS", 2.0)
    fetch_min_interval: float = _float("OPS_FETCH_MIN_INTERVAL", 1.0)
    #: Exotic bet types whose PRE-RACE price grid the daily refresh captures. Each one costs an
    #: extra request per race, so this is the volume dial: empty disables the capture entirely.
    #: These prices cannot be recovered later — a race not captured before it runs is lost for
    #: good, since `exotic_odds` only ever holds the dividend of the combination that came in.
    exotic_quote_bet_types: tuple[str, ...] = _csv(
        "OPS_EXOTIC_QUOTE_BET_TYPES", ("quinella", "wide", "trio")
    )
    #: 通過順 (corner passing order) is EMPTY on netkeiba on race night and appears about a day
    #: later — measured from archived pages: 2.4% of cells filled at lag 0, 99.8% at lag 1. Nothing
    #: ever went back for it, so every race day left `race_results.corner_orders` NULL forever
    #: unless a human happened to re-run that day by hand, starving the corner-trajectory,
    #: running-style and pace-scenario features.
    #:
    #: A day refresh therefore also patches recent days that still have holes. Gap-driven, NOT
    #: "re-fetch at lag 1": 2026-08-22 was still empty at lag 1, so a fixed schedule would have
    #: missed it and never looked again. Days: how far back to keep patching (0 disables). Races:
    #: the per-refresh cap, so one click can never balloon into an unbounded scrape.
    corner_backfill_days: int = _int("OPS_CORNER_BACKFILL_DAYS", 14)
    corner_backfill_max_races: int = _int("OPS_CORNER_BACKFILL_MAX_RACES", 36)
    #: Feature 137: a race refresh that WROTE win odds for a still-pending race queues a recompute
    #: of that race date's 期待回収率 (the value is win_prob × the odds it was computed from, so
    #: new odds make the stored one stale). Off-switch for the operator; default on.
    expected_return_on_refresh: bool = _bool("OPS_EXPECTED_RETURN_ON_REFRESH", True)
    #: Feature 137: absolute model directory handed to `horseracing_training market-ev`.
    market_ev_model_dir: str = _str("OPS_MARKET_EV_MODEL_DIR", str(_DEFAULT_MARKET_EV_MODEL_DIR))


CONFIG = OpsConfig()
