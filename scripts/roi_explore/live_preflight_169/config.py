"""Private explicit profile loading. Never use owner credentials or a database fallback."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
KEYS = {
    "DATABASE_URL",
    "HORSERACING_OBSERVER_PROFILE_VERSION",
    "HORSERACING_EXPECTED_DATABASE",
    "HORSERACING_EXPECTED_ROLE",
    "HORSERACING_ENDPOINT_CONFIRMED",
    "HORSERACING_NETKEIBA_MIN_INTERVAL_S",
    "HORSERACING_ROBOTS_CACHE_DIR",
}
BLOCKED_PG_KEYS = {
    "PGOPTIONS",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGPASSFILE",
    "PGTARGETSESSIONATTRS",
    "PGSSLMODE",
    "PGSYSCONFDIR",
}
PLACEHOLDERS = ("REPLACE_", "CHANGE_ME", "UNSET", "<", ">", "${")


@dataclass(repr=False)
class Profile:
    public: dict
    problems: list[str]
    url: object = field(default=None, repr=False)
    environment: dict = field(default_factory=dict, repr=False)
    robots_directory: Path | None = None
    interval: float | None = None

    def __repr__(self):
        return f"Profile(status={'BLOCKED' if self.problems else 'VALIDATED'})"


def load_profile(path=None, *, environ=None):
    environ = dict(os.environ if environ is None else environ)
    errors = []
    public = dict(
        profile_source="explicit_file" if path is not None else "current_environment",
        can_collect=False,
        owner_url_used=False,
        fallback_used=False,
    )
    if path is None:
        values = {k: environ.get(k) for k in KEYS}
    else:
        path = Path(path).absolute()
        public["profile_path"] = str(path)
        if path.is_symlink() or not path.is_file():
            return Profile(
                dict(public, status="MISSING_PROFILE"), ["profile_missing_or_symlink"]
            )
        permissions = stat.S_IMODE(path.stat().st_mode)
        public["profile_mode"] = oct(permissions)
        if permissions & 0o077:
            errors.append("profile_must_be_private_0600")
        try:
            body = path.read_text()
            values = dict(dotenv_values(path, interpolate=False))
            lines = [
                re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                for line in body.splitlines()
            ]
            names = [m[1] for m in lines if m]
            if len(names) != len(set(names)):
                errors.append("duplicate_profile_key")
            if set(values) - KEYS:
                errors.append("unrecognized_or_owner_keys_in_profile")
        except (OSError, UnicodeError):
            return Profile(
                dict(public, status="UNREADABLE_PROFILE"), ["profile_unreadable"]
            )
    inherited = [k for k in sorted(BLOCKED_PG_KEYS) if environ.get(k)]
    public["inherited_pg_override_keys"] = inherited
    if inherited:
        errors.append("inherited_libpq_overrides_require_removal")
    public["endpoint_confirmed"] = (
        values.get("HORSERACING_ENDPOINT_CONFIRMED") == "true"
    )
    if not public["endpoint_confirmed"]:
        errors.append("endpoint_not_explicitly_confirmed")
    if values.get("HORSERACING_OBSERVER_PROFILE_VERSION") != "169-v1":
        errors.append("profile_version_missing_or_wrong")
    url = None
    raw = values.get("DATABASE_URL")
    if not raw:
        errors.append("live_database_url_missing")
    elif any(token in raw for token in PLACEHOLDERS) or any(
        token in raw.lower() for token in ("replace_", "change-me", "change_me")
    ):
        errors.append("live_database_url_is_placeholder")
    else:
        try:
            url = make_url(raw)
            if url.drivername != "postgresql+psycopg":
                errors.append("native_psycopg_driver_required")
            if not url.username or not re.fullmatch(
                r"[a-z_][a-z0-9_]{0,62}", url.username
            ):
                errors.append("explicit_safe_login_required")
            if not url.password:
                errors.append("explicit_private_password_required")
            if url.host not in ("localhost", "127.0.0.1", "::1") or url.port is None:
                errors.append("explicit_loopback_endpoint_required_no_hostname_rewrite")
            if not url.database or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,62}", url.database
            ):
                errors.append("explicit_safe_database_required")
            if url.query:
                errors.append("dsn_query_options_require_separate_review")
            if url.username != values.get("HORSERACING_EXPECTED_ROLE"):
                errors.append("expected_login_mismatch")
            if url.database != values.get("HORSERACING_EXPECTED_DATABASE"):
                errors.append("expected_database_mismatch")
            public["endpoint"] = dict(
                driver=url.drivername,
                host=url.host,
                port=url.port,
                database=url.database,
                login=url.username,
                password_configured=bool(url.password),
            )
        except Exception:
            errors.append("invalid_database_url")
            url = None
    interval = None
    try:
        raw_interval = values.get("HORSERACING_NETKEIBA_MIN_INTERVAL_S")
        interval = float(raw_interval) if raw_interval else None
        if interval is None or not math.isfinite(interval) or interval < 1:
            raise ValueError("invalid_interval")
    except (ValueError, TypeError, OverflowError):
        errors.append("approved_source_interval_missing_or_invalid")
        interval = None
    public["source_interval_seconds"] = interval
    raw_cache = values.get("HORSERACING_ROBOTS_CACHE_DIR")
    directory = (
        ROOT / "artifacts/robots_cache"
        if raw_cache is None
        else Path(raw_cache.strip()).resolve()
        if raw_cache.strip()
        else None
    )
    public["robots_directory"] = str(directory) if directory else None
    public["status"] = (
        "BLOCKED_CONFIGURATION" if errors else "PROFILE_VALIDATED_NOT_COLLECTING"
    )
    public["public_config_sha256"] = hashlib.sha256(
        json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return Profile(public, errors, url, values, directory, interval)


@contextlib.contextmanager
def profile_environment(profile):
    """Use the same explicit settings for inspection; never evaluate an env file as shell code."""
    if profile.problems:
        raise ValueError("invalid_live_profile")
    if any(os.getenv(key) for key in BLOCKED_PG_KEYS):
        raise ValueError("libpq_environment_changed_after_validation")
    previous = {k: os.environ.get(k) for k in KEYS}
    try:
        for key in KEYS:
            value = profile.environment.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def init_profile(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError("profile_already_exists")
    body = (ROOT / "config/manual-observer.env.example").read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    return dict(
        profile_path=str(path.resolve()),
        mode=oct(stat.S_IMODE(path.stat().st_mode)),
        placeholder_only=True,
        can_collect=False,
        existing_config_overwritten=False,
    )
