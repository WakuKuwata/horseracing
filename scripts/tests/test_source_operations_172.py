"""Pure policy/ASGI/socket checks: no DB, source request, queue recovery or worker launch."""

import asyncio
import json
import socket

import pytest

from scripts.source_operations_172 import (
    CONFIG,
    PausedOps,
    apply_policy,
    load_policy,
    no_outbound_python_connections,
    spawn_detached,
)


def policy_file(tmp_path, **changes):
    value = json.loads(CONFIG.read_text())
    # The checked-in policy may legitimately carry the capture-only trio (174); the "partial
    # trio" cases below must start from a base without it, or they stop testing anything.
    for key in ("capture_only_dates", "capture_only_approved_by", "capture_only_approved_at"):
        value.pop(key, None)
    value.update(changes)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    return path


@pytest.mark.parametrize(
    "value", [0, 1, 59.9, True, False, "60", None, float("nan"), float("inf")]
)
def test_bad_source_interval_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        load_policy(policy_file(tmp_path, source_interval_seconds=value))


@pytest.mark.parametrize(
    "changes",
    [
        {"collection_paused": False},
        {"collection_paused": "true"},
        {"shared_limiter_enabled": False},
        {"shared_limiter_enabled": 1},
        {"schema_version": "unknown"},
        {"unknown": "key"},
        {"ops_fetch_min_interval_seconds": 61},
        {"ops_fetch_min_interval_seconds": float("nan")},
    ],
)
def test_policy_cannot_enable_collection_or_drift(tmp_path, changes):
    with pytest.raises(ValueError):
        load_policy(policy_file(tmp_path, **changes))


def test_duplicate_keys_rejected(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"collection_paused":true,"collection_paused":false}')
    with pytest.raises(ValueError, match="duplicate_policy_key"):
        load_policy(path)


@pytest.mark.parametrize(
    "key", ["HORSERACING_NETKEIBA_MIN_INTERVAL_S", "OPS_FETCH_MIN_INTERVAL"]
)
@pytest.mark.parametrize("value", ["120", "nan", "inf", "-1", "bad"])
def test_inherited_contract_never_silently_shortened_or_invalid(
    monkeypatch, key, value
):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        apply_policy(load_policy())


@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_disabled_shared_limiter_refuses(monkeypatch, value):
    monkeypatch.setenv("HORSERACING_SHARED_LIMITER", value)
    with pytest.raises(ValueError, match="shared_limiter_disabled"):
        apply_policy(load_policy())


def test_policy_applies_all_intervals_and_pause(monkeypatch):
    import os

    for key in (
        "HORSERACING_NETKEIBA_MIN_INTERVAL_S",
        "OPS_FETCH_MIN_INTERVAL",
        "HORSERACING_SHARED_LIMITER",
        "HORSERACING_COLLECTION_MODE",
        "OPS_CAPTURE_ON_AUTO_PREDICT",
    ):
        monkeypatch.delenv(key, raising=False)
    apply_policy(load_policy())
    assert (
        os.environ["HORSERACING_NETKEIBA_MIN_INTERVAL_S"]
        == os.environ["OPS_FETCH_MIN_INTERVAL"]
        == "60"
    )
    assert os.environ["HORSERACING_COLLECTION_MODE"] == "paused"
    assert os.environ["HORSERACING_SHARED_LIMITER"] == "1"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "TRACE"])
def test_ops_mutations_rejected_before_body_dependencies_or_app(method):
    sent = []

    async def forbidden(*args):
        pytest.fail("must not reach application, body, DB or enqueue")

    async def send(message):
        sent.append(message)

    asyncio.run(
        PausedOps(forbidden)({"type": "http", "method": method}, forbidden, send)
    )
    assert sent[0]["status"] == 503
    assert json.loads(sent[1]["body"])["code"] == "collection_paused"


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "http", "method": "GET"},
        {"type": "http", "method": "HEAD"},
        {"type": "http", "method": "OPTIONS"},
        {"type": "lifespan"},
    ],
)
def test_read_and_lifespan_delegate_without_starting_worker(scope):
    calls = []

    async def app(scope, receive, send):
        calls.append(scope)

    asyncio.run(PausedOps(app)(scope, None, None))
    assert calls == [scope]


def test_python_outbound_connect_is_blocked_and_restored():
    original = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    with no_outbound_python_connections():
        with socket.socket() as sock:
            with pytest.raises(RuntimeError, match="collection_paused"):
                sock.connect(("127.0.0.1", 1))
            with pytest.raises(RuntimeError, match="collection_paused"):
                sock.connect_ex(("127.0.0.1", 1))
        with pytest.raises(RuntimeError, match="collection_paused"):
            socket.create_connection(("127.0.0.1", 1))
    assert (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.create_connection,
    ) == original


def test_detached_service_has_separate_session_and_no_secret_arguments(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import scripts.source_operations_172 as module

    args = SimpleNamespace(
        component="api",
        policy=CONFIG,
        report=tmp_path / "report.json",
        log=tmp_path / "server.log",
        pidfile=tmp_path / "server.pid",
    )
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        args.report.write_text("{}")
        return SimpleNamespace(pid=123456, poll=lambda: None)

    monkeypatch.setenv("DATABASE_URL", "private-value-not-for-arguments")
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    assert spawn_detached(args) == 0
    command, options = calls[0]
    assert options["start_new_session"] is options["close_fds"] is True
    assert options["stdin"] == module.subprocess.DEVNULL
    assert "private-value" not in " ".join(command)
    assert options["env"]["DATABASE_URL"] == "private-value-not-for-arguments"
    assert args.pidfile.read_text() == "123456\n"


# ---- Feature 174: capture-only days -------------------------------------------------------


def test_capture_only_keys_accepted_together(tmp_path):
    policy = load_policy(
        policy_file(
            tmp_path,
            capture_only_dates=["2026-11-07", "2026-11-08"],
            capture_only_approved_by="owner",
            capture_only_approved_at="2026-10-29T10:00:00+09:00",
        )
    )
    assert policy["collection_paused"] is True
    assert policy["capture_only_dates"] == ["2026-11-07", "2026-11-08"]


@pytest.mark.parametrize(
    "changes",
    [
        {"capture_only_dates": ["2026-11-07"]},  # partial trio
        {"capture_only_dates": ["2026-11-07"], "capture_only_approved_by": "owner"},
        {"capture_only_dates": "2026-11-07", "capture_only_approved_by": "owner",
         "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
        {"capture_only_dates": ["2026/11/07"], "capture_only_approved_by": "owner",
         "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
        {"capture_only_dates": ["2026-11-07", "2026-11-07"], "capture_only_approved_by": "owner",
         "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
        {"capture_only_dates": ["2026-11-07"], "capture_only_approved_by": " ",
         "capture_only_approved_at": "2026-10-29T10:00:00+09:00"},
        {"capture_only_dates": ["2026-11-07"], "capture_only_approved_by": "owner",
         "capture_only_approved_at": "yesterday"},
        {"capture_only_dates": ["2026-11-07"], "capture_only_approved_by": "owner",
         "capture_only_approved_at": "2026-10-29T10:00:00+09:00", "collection_paused": False},
    ],
)
def test_capture_only_keys_rejected_when_incomplete_or_invalid(tmp_path, changes):
    with pytest.raises(ValueError):
        load_policy(policy_file(tmp_path, **changes))
