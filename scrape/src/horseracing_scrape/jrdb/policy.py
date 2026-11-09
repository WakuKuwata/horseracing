"""起動時検証 for the JRDB CLIs (Feature 178, T031; contracts/cli.md「共通の起動時検証」).

Same shape as 176 ``jra/policy.py``: every check runs BEFORE the first request, a failure is a
typed :class:`JrdbStartupRefused` with a summary code, and the wrapper (``scripts/jrdb_ops.sh``)
is a convenience, not the safety boundary.

Checks:
1. policy file readable and ``jrdb_enabled`` is exactly ``true`` (``jrdb_disabled``);
2. interval: policy ``jrdb_interval_seconds`` (default 5) ≤ env ``HORSERACING_JRDB_MIN_INTERVAL_S``
   (the env may lengthen, never shorten; unset → written from the policy), floor 2 s;
3. credentials present when the command talks to the member site (``credentials_missing`` —
   values are never read into the message);
4. back-number requests outside the blackout (``archive_blackout``);
5. profile ↔ connection binding: ``jrdb_canonical`` must not target the production database
   URL (``profile_db_mismatch``; codex D20 #9).
"""

from __future__ import annotations

import datetime
import json
import math
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .blackout import is_archive_blackout
from .errors import JrdbCredentialsMissing, JrdbStartupRefused
from .fetch import (
    DEFAULT_INTERVAL_S,
    INTERVAL_ENV,
    MIN_INTERVAL_FLOOR_S,
    Credentials,
    credentials_from_env,
)

ENABLED_KEY = "jrdb_enabled"
INTERVAL_KEY = "jrdb_interval_seconds"
BLACKOUT_KEY = "jrdb_archive_blackout"
RACE_DAYS_KEY = "capture_only_dates"
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"


@dataclass(frozen=True)
class JrdbStartupContext:
    interval_s: float
    policy_path: Path
    credentials: Credentials | None
    policy_race_days: tuple[datetime.date, ...]
    blackout_enforced: bool


def read_policy(policy_path: Path) -> dict:
    try:
        doc = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise JrdbStartupRefused(
            "policy_invalid", f"policy file not found: {policy_path}"
        ) from None
    except (OSError, json.JSONDecodeError) as exc:
        raise JrdbStartupRefused(
            "policy_invalid", f"policy file unreadable: {policy_path}: {exc}"
        ) from None
    if not isinstance(doc, dict):
        raise JrdbStartupRefused("policy_invalid", "policy file is not an object")
    return doc


def require_enabled(doc: dict) -> None:
    from ..netkeiba_policy import PolicyFlagInvalid, read_flag  # the shared flag reader (T067)

    try:
        enabled = read_flag(doc, ENABLED_KEY, False)
    except PolicyFlagInvalid:
        enabled = False  # a non-boolean never means "on"
    if not enabled:
        raise JrdbStartupRefused(
            "jrdb_disabled",
            f"{ENABLED_KEY} is not true in the policy; the JRDB route sends nothing",
        )


def policy_interval(doc: dict) -> float:
    raw = doc.get(INTERVAL_KEY, DEFAULT_INTERVAL_S)
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(float(raw)):
        raise JrdbStartupRefused("policy_invalid", f"{INTERVAL_KEY} must be a finite number")
    value = float(raw)
    if value < MIN_INTERVAL_FLOOR_S:
        raise JrdbStartupRefused(
            "policy_invalid",
            f"{INTERVAL_KEY}={value:g} is below the floor of {MIN_INTERVAL_FLOOR_S:g}s",
        )
    return value


def reconcile_env_interval(policy_value: float, env: dict[str, str]) -> float:
    raw = env.get(INTERVAL_ENV)
    if raw is None or not raw.strip():
        env[INTERVAL_ENV] = f"{policy_value:g}"
        return policy_value
    try:
        current = float(raw)
    except ValueError:
        raise JrdbStartupRefused(
            "would_shorten_source_interval", f"{INTERVAL_ENV} is not a number"
        ) from None
    if not math.isfinite(current) or current < policy_value:
        raise JrdbStartupRefused(
            "would_shorten_source_interval",
            f"{INTERVAL_ENV}={raw!r} is shorter than the policy's {policy_value:g}s",
        )
    return current


def policy_race_days(doc: dict) -> tuple[datetime.date, ...]:
    out: list[datetime.date] = []
    for s in doc.get(RACE_DAYS_KEY, []) or []:
        try:
            out.append(datetime.date.fromisoformat(str(s)))
        except ValueError:
            continue
    return tuple(out)


def assert_profile_binding(
    profile: str, database_url: str | None, production_url: str | None
) -> None:
    """``jrdb_canonical`` may never point at the production database (codex D20 #9)."""

    if profile != "jrdb_canonical":
        return
    if not database_url:
        raise JrdbStartupRefused(
            "profile_db_mismatch", "profile jrdb_canonical requires an explicit --db-url"
        )
    if production_url and database_url.strip() == production_url.strip():
        raise JrdbStartupRefused(
            "profile_db_mismatch",
            "profile jrdb_canonical must not target the production DATABASE_URL",
        )


def validate_startup(
    *,
    policy_path: Path | None = None,
    env: dict[str, str] | None = None,
    needs_credentials: bool,
    is_archive: bool,
    now_jst: datetime.datetime | None = None,
    db_race_days: Collection[datetime.date] = (),
) -> JrdbStartupContext:
    import os

    env_map = os.environ if env is None else env
    path = policy_path or DEFAULT_POLICY_PATH
    doc = read_policy(path)
    require_enabled(doc)
    interval = reconcile_env_interval(policy_interval(doc), env_map)
    creds: Credentials | None = None
    if needs_credentials:
        try:
            creds = credentials_from_env(env_map)
        except JrdbCredentialsMissing as exc:
            raise JrdbStartupRefused("credentials_missing", str(exc)) from None
    race_days = policy_race_days(doc)
    enforce = doc.get(BLACKOUT_KEY, True) is not False
    if is_archive and enforce:
        now = now_jst or datetime.datetime.now(datetime.UTC)
        if is_archive_blackout(now, set(race_days) | set(db_race_days)):
            raise JrdbStartupRefused(
                "archive_blackout",
                "back-number downloads are not allowed on race days 08:00-19:00 JST (FR-018)",
            )
    return JrdbStartupContext(
        interval_s=interval,
        policy_path=path,
        credentials=creds,
        policy_race_days=race_days,
        blackout_enforced=enforce,
    )


__all__ = [
    "BLACKOUT_KEY",
    "DEFAULT_POLICY_PATH",
    "ENABLED_KEY",
    "INTERVAL_KEY",
    "JrdbStartupContext",
    "assert_profile_binding",
    "policy_interval",
    "policy_race_days",
    "read_policy",
    "reconcile_env_interval",
    "require_enabled",
    "validate_startup",
]
