"""Feature 178 US4 (T067): the policy switch that stops netkeiba (FR-014・research D13).

``config/source-operations.json`` may carry ``netkeiba_enabled`` (default ``true``). Every place
that builds a netkeiba fetcher — the scrape CLI's ``_make_fetcher``, the ops worker's
``make_fetcher``, the live capture fetcher — reads it at START (CLI start / job start: no restart,
172's ``collection_paused`` shape) and, when it is ``false``, raises ``NetkeibaDisabled`` BEFORE
any HTTP client exists. The callers turn that into a SKIPPED job / a skipped command with zero
requests. The code is never deleted: ``true`` restores the old behaviour byte for byte.

``jrdb_enabled`` (default ``false``) is read by the same ``read_flag`` (jrdb/policy.py).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "source-operations.json"
#: tests and replicas point the readers at another file; production leaves it unset
POLICY_PATH_ENV = "HORSERACING_SOURCE_POLICY_PATH"
NETKEIBA_KEY = "netkeiba_enabled"
JRDB_KEY = "jrdb_enabled"
EMERGENCY_KEY = "jra_emergency_entries"


class PolicyFlagInvalid(ValueError):
    """A flag that is present but not a boolean: refused, never coerced."""


class NetkeibaDisabled(RuntimeError):
    """``netkeiba_enabled`` is false: the netkeiba route sends nothing."""

    code = "netkeiba_disabled"

    def __init__(self, policy_path: Path | str):
        self.policy_path = str(policy_path)
        super().__init__(f"{NETKEIBA_KEY} is false in {policy_path}; netkeiba sends nothing")


def policy_path(explicit: Path | str | None = None) -> Path:
    """The file every reader uses. The env override (tests, replicas) must be ABSOLUTE so a
    worker, its subprocesses and the audit cannot resolve a relative name to different files
    (codex D25 Q1-4)."""

    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(POLICY_PATH_ENV, "").strip()
    if not env:
        return DEFAULT_POLICY_PATH
    path = Path(env)
    if not path.is_absolute():
        raise PolicyFlagInvalid(f"{POLICY_PATH_ENV} must be an absolute path, got {env!r}")
    return path


def policy_fingerprint(path: Path | str | None = None) -> dict:
    """``{path, sha256}`` of the policy file as read — written into job summaries and the audit so
    a stop shown on one screen and a request sent from another process can be told apart."""

    import hashlib

    p = policy_path(path)
    try:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        sha = None
    return {"path": str(p), "sha256": sha}


def read_policy_doc(path: Path | str | None = None) -> dict:
    """The policy file as data. A MISSING file is an empty policy (every flag at its default:
    the netkeiba route keeps working where no policy was ever written); an unreadable one is an
    error (a half-written policy must not silently mean 'enabled')."""

    p = policy_path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(doc, dict):
        raise PolicyFlagInvalid(f"policy file is not an object: {p}")
    return doc


def read_flag(policy: Mapping, key: str, default: bool) -> bool:
    """A boolean policy flag: absent → ``default``; present but not a bool → refused."""

    if key not in policy:
        return default
    value = policy[key]
    if not isinstance(value, bool):
        raise PolicyFlagInvalid(f"{key} must be true or false, got {value!r}")
    return value


def read_netkeiba_enabled(path: Path | str | None = None) -> bool:
    return read_flag(read_policy_doc(path), NETKEIBA_KEY, True)


def assert_netkeiba_enabled(path: Path | str | None = None) -> None:
    """Raise ``NetkeibaDisabled`` when the policy says so. Called before a fetcher is built AND
    (through ``gate_pre_request``) before every request the fetcher sends."""

    p = policy_path(path)
    if not read_flag(read_policy_doc(p), NETKEIBA_KEY, True):
        raise NetkeibaDisabled(p)


def gate_pre_request(inner=None):
    """Compose the per-request hook of a netkeiba fetcher with the policy check, so a fetcher
    built while enabled stops at the next request once the policy flips (codex D25 Q1-1). The
    file is re-read on every request: one small JSON per request is nothing next to the shared
    limiter's interval."""

    def _pre_request(url: str) -> None:
        assert_netkeiba_enabled()
        if inner is not None:
            inner(url)

    _pre_request.inner = inner  # type: ignore[attr-defined]  # the seam tests look through the gate
    return _pre_request


__all__ = [
    "DEFAULT_POLICY_PATH",
    "EMERGENCY_KEY",
    "JRDB_KEY",
    "NETKEIBA_KEY",
    "POLICY_PATH_ENV",
    "NetkeibaDisabled",
    "PolicyFlagInvalid",
    "assert_netkeiba_enabled",
    "gate_pre_request",
    "policy_fingerprint",
    "policy_path",
    "read_flag",
    "read_netkeiba_enabled",
    "read_policy_doc",
]
