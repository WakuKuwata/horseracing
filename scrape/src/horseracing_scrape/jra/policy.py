"""起動時検証(Feature 176, contracts/cli.md「共通の起動時検証」・research D3).

JRA CLI は **1 件も送る前に** 自分で確認する:

1. policy ファイル(``config/source-operations.json``)が読め、``jra_official_interval_seconds`` が
   数値・有限・下限(10 秒)以上であること。
2. env ``HORSERACING_JRA_MIN_INTERVAL_S`` が policy 値以上であること。
   無ければ policy 値を自分で写す。
   短い値が継承されていれば拒否(172 と同じ「短縮禁止」)。長ければそれを採る。
3. 共有リミッタが無効化されていない(``HORSERACING_SHARED_LIMITER=0`` は JRA では拒否)。
4. DB に到達できる(``background_policy`` が ``NoSharedLimiter`` を投げたらそのまま拒否)。

どれかが落ちたら ``StartupRefused``(exit 2)で HTTP は 0 件。wrapper(``scripts/jra_ops.sh``)を通さず
起動されても同じ検査が走る=wrapper は便利のためで安全境界ではない。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ..politeness import (
    JRA_MIN_INTERVAL_FLOOR_S,
    JRA_SOURCE_INTERVAL_ENV,
    SHARED_LIMITER_ENV,
    NoSharedLimiter,
    RequestPoliteness,
    background_policy,
    jra_source_interval_s,
)
from .errors import SourceIntervalUnconfigured

POLICY_KEY = "jra_official_interval_seconds"
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"


class StartupRefused(RuntimeError):
    """A startup check failed: nothing was sent. ``code`` is the summary key (contracts/cli.md)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class StartupContext:
    interval_s: float
    policy_path: Path
    policy: RequestPoliteness


def read_policy_interval(policy_path: Path) -> float:
    """``jra_official_interval_seconds`` from the policy file, validated. Raises StartupRefused."""
    try:
        doc = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StartupRefused("policy_invalid", f"policy file not found: {policy_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StartupRefused(
            "policy_invalid", f"policy file unreadable: {policy_path}: {exc}"
        ) from exc
    if not isinstance(doc, dict) or POLICY_KEY not in doc:
        raise StartupRefused(
            "policy_invalid",
            f"{POLICY_KEY} is missing from {policy_path}; the JRA route sends nothing without it",
        )
    raw = doc[POLICY_KEY]
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise StartupRefused("policy_invalid", f"{POLICY_KEY} must be a number, got {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise StartupRefused("policy_invalid", f"{POLICY_KEY} must be finite, got {raw!r}")
    if value < JRA_MIN_INTERVAL_FLOOR_S:
        raise StartupRefused(
            "policy_invalid",
            f"{POLICY_KEY}={raw!r} is below the floor of {JRA_MIN_INTERVAL_FLOOR_S:g}s",
        )
    return value


def reconcile_env_interval(policy_interval: float, env: dict[str, str]) -> float:
    """Make the env interval agree with the policy. Returns the effective interval.

    * unset → write the policy value into ``env``.
    * longer → keep the longer one (a wrapper may lengthen, never shorten).
    * shorter / invalid → ``StartupRefused('would_shorten_source_interval')``.
    """
    raw = env.get(JRA_SOURCE_INTERVAL_ENV)
    if raw is None or not raw.strip():
        env[JRA_SOURCE_INTERVAL_ENV] = f"{policy_interval:g}"
        return policy_interval
    try:
        current = float(raw)
    except ValueError as exc:
        raise StartupRefused(
            "would_shorten_source_interval", f"{JRA_SOURCE_INTERVAL_ENV}={raw!r} is not a number"
        ) from exc
    if not math.isfinite(current) or current < policy_interval:
        raise StartupRefused(
            "would_shorten_source_interval",
            f"{JRA_SOURCE_INTERVAL_ENV}={raw!r} is shorter than the policy's {policy_interval:g}s",
        )
    return current


def validate_startup(
    *,
    policy_path: Path | None = None,
    database_url: str | None = None,
    env: dict[str, str] | None = None,
) -> StartupContext:
    """Run all four checks and return the shared politeness policy for this run."""
    env_map = os.environ if env is None else env
    path = policy_path or DEFAULT_POLICY_PATH
    interval = read_policy_interval(path)
    interval = reconcile_env_interval(interval, env_map)
    if env_map.get(SHARED_LIMITER_ENV, "").strip().lower() in {"0", "false", "no"}:
        raise StartupRefused(
            "NoSharedLimiter",
            f"{SHARED_LIMITER_ENV} disables the shared limiter; "
            "the JRA route refuses to run without it",
        )
    # The value the limiter will read must be valid (same mapping the limiter reads from).
    try:
        jra_source_interval_s(env_map.get(JRA_SOURCE_INTERVAL_ENV))
    except SourceIntervalUnconfigured as exc:
        raise StartupRefused("policy_invalid", str(exc)) from exc
    try:
        policy = background_policy(min_interval_s=interval, database_url=database_url)
        if policy is None:
            raise NoSharedLimiter("shared limiter unavailable")
        policy.ping()  # engines are lazy; prove the limiter's database answers before any HTTP
    except Exception as exc:  # NoSharedLimiter or any DB failure: 0 requests
        raise StartupRefused("NoSharedLimiter", str(exc)) from exc
    # JRA never waits out a cooldown (FR-005): refuse and stop the run instead.
    policy.wait_out_cooldown = False
    return StartupContext(interval_s=interval, policy_path=path, policy=policy)
