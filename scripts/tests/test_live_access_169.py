"""No DB or network: current robots/throttle state cannot authorize live collection."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json

import pytest

from scripts.roi_explore.live_preflight_169.access import (
    MAX_BODY_BYTES,
    MAX_CACHE_FILE_BYTES,
    ORIGIN,
    inspect_robots,
    throttle_check,
)

NOW = dt.datetime(2026, 10, 27, 5, tzinfo=dt.UTC)


def cache(tmp_path, *, fetched=None, body=b"User-agent: *\nAllow: /\n", **updates):
    doc = dict(
        schema_version=1,
        origin=ORIGIN,
        outcome="rules",
        status=200,
        fetched_at=(fetched or NOW - dt.timedelta(hours=1)).isoformat(),
        body=body if isinstance(body, str) else base64.b64encode(body).decode(),
    )
    doc.update(updates)
    path = tmp_path / (hashlib.sha256(ORIGIN.encode()).hexdigest()[:20] + ".json")
    path.write_text(json.dumps(doc))
    return path


def row(**updates):
    result = dict(
        domain="netkeiba.com",
        next_allowed_at=(NOW - dt.timedelta(seconds=1)).isoformat(),
        updated_at=(NOW - dt.timedelta(seconds=2)).isoformat(),
        blocked_until=None,
    )
    result.update(updates)
    return result


def test_missing_or_disabled_cache_never_creates_a_directory(tmp_path):
    directory = tmp_path / "missing"
    assert inspect_robots(directory, NOW)["blocking_reasons"] == [
        "robots_cache_missing"
    ]
    assert inspect_robots(None, NOW)["blocking_reasons"] == ["robots_cache_disabled"]
    assert not directory.exists()


def test_allow_is_current_fact_only_without_collection(tmp_path):
    path = cache(tmp_path)
    before = path.read_bytes()
    result = inspect_robots(tmp_path, NOW)
    assert result["current_ready"] is True and result["allowed"] is True
    assert result["launch_ready"] is None and result["launch_recheck_required"] is True
    assert result["can_collect"] is False and result["cache_refreshed"] is False
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]
    assert "body" not in result


@pytest.mark.parametrize(
    "delta,ready,reason",
    [
        (dt.timedelta(hours=23, minutes=59, seconds=59), True, None),
        (dt.timedelta(hours=24), False, "robots_cache_expired"),
        (dt.timedelta(seconds=-1), False, "robots_capture_from_future"),
    ],
)
def test_cache_ttl_and_future_capture_boundaries(tmp_path, delta, ready, reason):
    cache(tmp_path, fetched=NOW - delta)
    result = inspect_robots(tmp_path, NOW)
    assert result["current_ready"] is ready
    if reason:
        assert reason in result["blocking_reasons"]


def test_fresh_disallow_is_blocked(tmp_path):
    cache(tmp_path, body=b"User-agent: *\nDisallow: /api/\n")
    result = inspect_robots(tmp_path, NOW)
    assert result["current_fresh"] is True and result["current_ready"] is False
    assert result["allowed"] is False


@pytest.mark.parametrize("code", [404, 410])
def test_explicit_absence_with_correct_status_allows_now(tmp_path, code):
    cache(tmp_path, body=b"", outcome="absent", status=code)
    assert inspect_robots(tmp_path, NOW)["current_ready"] is True


@pytest.mark.parametrize(
    "updates",
    [
        dict(schema_version=True),
        dict(schema_version=2),
        dict(origin="https://db.netkeiba.com"),
        dict(status=403),
        dict(status=True),
        dict(outcome="absent", status=200),
        dict(outcome="rules", status=404),
        dict(outcome="transient", status=500),
        dict(body="%%%%"),
        dict(fetched_at="2026-10-27T00:00:00"),
        dict(fetched_at="9999-12-31T23:59:59+00:00"),
    ],
)
def test_invalid_cache_metadata_blocks(tmp_path, updates):
    cache(tmp_path, **updates)
    result = inspect_robots(tmp_path, NOW)
    assert result["current_ready"] is False and result["blocking_reasons"] == [
        "robots_cache_invalid"
    ]


def test_absence_must_not_carry_rules_body(tmp_path):
    cache(tmp_path, outcome="absent", status=404)
    assert inspect_robots(tmp_path, NOW)["current_ready"] is False


def test_oversize_body_and_file_are_rejected(tmp_path):
    path = cache(tmp_path, body=b"x" * (MAX_BODY_BYTES + 1))
    assert inspect_robots(tmp_path, NOW)["current_ready"] is False
    path.write_bytes(b"x" * (MAX_CACHE_FILE_BYTES + 1))
    assert inspect_robots(tmp_path, NOW)["current_ready"] is False


def test_duplicate_json_key_is_rejected(tmp_path):
    path = cache(tmp_path)
    path.write_text(
        path.read_text().replace('"status": 200', '"status": 403, "status": 200')
    )
    assert inspect_robots(tmp_path, NOW)["current_ready"] is False


def test_cache_expires_exactly_at_launch(tmp_path):
    cache(tmp_path, fetched=NOW - dt.timedelta(hours=23))
    result = inspect_robots(tmp_path, NOW, NOW + dt.timedelta(hours=1))
    assert result["current_ready"] is True and result["launch_ready"] is False
    assert result["status"] == "BLOCKED_AT_PLANNED_LAUNCH"


def test_planned_launch_within_ttl_still_requires_actual_url_recheck(tmp_path):
    cache(tmp_path)
    result = inspect_robots(tmp_path, NOW, NOW + dt.timedelta(minutes=1))
    assert result["launch_ready"] is True and result["can_collect"] is False
    assert result["actual_request_url_recheck_required"] is True


@pytest.mark.parametrize(
    "now,launch",
    [
        (dt.datetime(2026, 10, 27), None),
        (NOW, dt.datetime(2026, 10, 27)),
        (NOW, NOW - dt.timedelta(seconds=1)),
    ],
)
def test_bad_check_times_fail_closed(tmp_path, now, launch):
    assert inspect_robots(tmp_path, now, launch)["status"] == "INVALID_TIME"
    assert throttle_check([row()], now, 1, launch)["status"] == "INVALID_TIME"


def test_throttle_current_clear_does_not_reserve_or_predict_future():
    result = throttle_check([row()], NOW, 60, NOW + dt.timedelta(days=1))
    assert result["current_ready"] is True and result["backlog_seconds"] == 0
    assert (
        result["launch_ready"] is None
        and result["future_contention_unconfirmed"] is True
    )
    assert (
        result["can_collect"]
        is result["slot_reserved"]
        is result["cooldown_cleared"]
        is False
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row(), row()],
        [dict(domain="https://race.netkeiba.com")],
        None,
        [None],
    ],
)
def test_missing_duplicate_or_wrong_source_state_does_not_pass(rows):
    assert throttle_check(rows, NOW, 1)["current_ready"] is False


@pytest.mark.parametrize(
    "interval", [None, True, "60", float("nan"), float("inf"), 0, -1, 0.999]
)
def test_invalid_or_unconfirmed_source_interval_blocks(interval):
    assert throttle_check([row()], NOW, interval)["current_ready"] is False


@pytest.mark.parametrize(
    "wait,ready", [(0, True), (3, True), (3.000001, False), (60, False)]
)
def test_current_backlog_max_wait_boundary(wait, ready):
    result = throttle_check(
        [row(next_allowed_at=(NOW + dt.timedelta(seconds=wait)).isoformat())], NOW, 60
    )
    assert result["current_ready"] is ready


@pytest.mark.parametrize(
    "until,ready",
    [
        (NOW, True),
        (NOW - dt.timedelta(seconds=1), True),
        (NOW + dt.timedelta(seconds=1), False),
    ],
)
def test_active_cooldown_boundary(until, ready):
    result = throttle_check([row(blocked_until=until.isoformat())], NOW, 1)
    assert result["current_ready"] is ready


@pytest.mark.parametrize(
    "updates",
    [
        dict(updated_at=(NOW + dt.timedelta(microseconds=1)).isoformat()),
        dict(next_allowed_at="unknown"),
        dict(updated_at="2026-10-27T00:00:00"),
        dict(blocked_until="unknown"),
    ],
)
def test_malformed_or_future_state_fails_closed(updates):
    assert throttle_check([row(**updates)], NOW, 1)["current_ready"] is False


def test_missing_required_state_field_is_unconfirmed():
    data = row()
    del data["blocked_until"]
    assert throttle_check([data], NOW, 1)["current_ready"] is False
