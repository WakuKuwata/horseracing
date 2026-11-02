"""176 T047: the JRA CLI's startup checks refuse before any HTTP (contracts/cli.md)."""

from __future__ import annotations

import json

import pytest

from horseracing_scrape.jra.policy import (
    POLICY_KEY,
    StartupRefused,
    read_policy_interval,
    reconcile_env_interval,
    validate_startup,
)
from horseracing_scrape.politeness import JRA_SOURCE_INTERVAL_ENV, SHARED_LIMITER_ENV


def _policy(tmp_path, value, extra=None):
    doc = {"schema_version": "172-v1", "collection_paused": False}
    if value is not None:
        doc[POLICY_KEY] = value
    doc.update(extra or {})
    p = tmp_path / "source-operations.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_policy_interval_valid(tmp_path):
    assert read_policy_interval(_policy(tmp_path, 10)) == 10.0
    assert read_policy_interval(_policy(tmp_path, 30.5)) == 30.5


@pytest.mark.parametrize("value", [None, "10", True, 9.99, float("inf"), -1])
def test_policy_interval_invalid(tmp_path, value):
    with pytest.raises(StartupRefused) as ei:
        read_policy_interval(_policy(tmp_path, value))
    assert ei.value.code == "policy_invalid"


def test_old_policy_without_the_key_is_refused_by_the_jra_cli_only(tmp_path):
    """The reader of 172 accepts the key as OPTIONAL; it is the JRA CLI that requires it."""
    with pytest.raises(StartupRefused):
        read_policy_interval(_policy(tmp_path, None))


def test_missing_file_and_bad_json(tmp_path):
    with pytest.raises(StartupRefused):
        read_policy_interval(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(StartupRefused):
        read_policy_interval(bad)


def test_env_reconciliation():
    env: dict[str, str] = {}
    assert reconcile_env_interval(10.0, env) == 10.0
    assert env[JRA_SOURCE_INTERVAL_ENV] == "10"
    env = {JRA_SOURCE_INTERVAL_ENV: "60"}
    assert reconcile_env_interval(10.0, env) == 60.0  # longer inherited value wins
    for bad in ("5", "abc", "inf"):
        with pytest.raises(StartupRefused) as ei:
            reconcile_env_interval(10.0, {JRA_SOURCE_INTERVAL_ENV: bad})
        assert ei.value.code == "would_shorten_source_interval"


def test_shared_limiter_disabled_is_refused(tmp_path):
    env = {SHARED_LIMITER_ENV: "0"}
    with pytest.raises(StartupRefused) as ei:
        validate_startup(policy_path=_policy(tmp_path, 10), env=env, database_url="postgresql://x")
    assert ei.value.code == "NoSharedLimiter"


def test_unreachable_database_is_refused_before_any_http(tmp_path):
    env: dict[str, str] = {}
    with pytest.raises(StartupRefused) as ei:
        validate_startup(
            policy_path=_policy(tmp_path, 10),
            env=env,
            database_url="postgresql+psycopg://u:p@127.0.0.1:1/nope",
        )
    assert ei.value.code == "NoSharedLimiter"
