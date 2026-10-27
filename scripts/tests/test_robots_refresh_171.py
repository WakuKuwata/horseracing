"""Network-free tests of the fixed robots-only boundary and fail-closed outcomes."""

import contextlib
import json

import pytest

from horseracing_scrape.robots_cache import RobotsCache
from scripts.roi_explore.robots_refresh_171 import (
    URL,
    ORIGIN,
    MAX_BODY_BYTES,
    exclusive_cache_lock,
    request_once,
)


class Policy:
    def __init__(self, blocked=False):
        self.calls = []
        self.refusals = []
        self.blocked = blocked

    def pre_request(self, url):
        self.calls.append(url)
        if self.blocked:
            raise ValueError("blocked")

    def record_refusal(self, refusal):
        self.refusals.append(refusal.status_code)


class Client:
    def __init__(self, code=200, body=b"User-agent: *\nAllow: /\n", broken=False):
        self.status_code = code
        self.body = body
        self.headers = {}
        self.calls = []
        self.broken = broken

    @contextlib.contextmanager
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.broken:
            raise OSError("offline")
        yield self

    def iter_bytes(self):
        yield self.body


def setup(tmp_path, **kwargs):
    directory = tmp_path / "cache"
    directory.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    return Client(**kwargs), Policy(), RobotsCache(directory), output


@pytest.mark.parametrize("code", [200, 404, 410])
def test_one_fixed_url_and_durable_cache(tmp_path, code):
    client, policy, cache, output = setup(tmp_path, code=code)
    result = request_once(client, policy, cache, output)
    assert client.calls == [("GET", URL, {"follow_redirects": False})]
    assert policy.calls == [URL]
    assert result["http_requests"] == 1
    assert result["race_data_collection"] is False
    assert result["can_collect_race_data"] is False
    assert result["quote_max_age_seconds"] == 1800
    assert result["both_predictions_durable_before_decision_required"] is True
    assert cache.get(ORIGIN).status == code
    assert (
        json.loads((output / "robots-cache-snapshot.json").read_text())["status"]
        == code
    )


@pytest.mark.parametrize("code", [301, 302, 307, 401, 500, 503])
def test_redirects_and_failures_never_cache_or_retry(tmp_path, code):
    client, policy, cache, output = setup(tmp_path, code=code)
    with pytest.raises(ValueError, match="robots_http_not_cacheable"):
        request_once(client, policy, cache, output)
    assert len(client.calls) == len(policy.calls) == 1
    assert not list(cache.directory.glob("*.json"))


@pytest.mark.parametrize("code", [400, 403, 429])
def test_refusals_persist_cooldown_without_retry(tmp_path, code):
    client, policy, cache, output = setup(tmp_path, code=code)
    with pytest.raises(ValueError, match="source_refused"):
        request_once(client, policy, cache, output)
    assert policy.refusals == [code]
    assert len(client.calls) == 1 and cache.get(ORIGIN) is None


def test_disallow_refreshes_but_cannot_authorize_race_capture(tmp_path):
    client, policy, cache, output = setup(
        tmp_path, body=b"User-agent: *\nDisallow: /\n"
    )
    result = request_once(client, policy, cache, output)
    assert result["robots"]["current_fresh"] is True
    assert result["robots"]["current_ready"] is False
    assert result["can_collect_race_data"] is False


def test_reservation_refusal_means_zero_http(tmp_path):
    client, _, cache, output = setup(tmp_path)
    with pytest.raises(ValueError, match="blocked"):
        request_once(client, Policy(blocked=True), cache, output)
    assert client.calls == [] and not list(output.iterdir())


def test_oversize_preserves_old_cache_timestamp(tmp_path):
    client, policy, cache, output = setup(tmp_path, body=b"x" * (MAX_BODY_BYTES + 1))
    cache.put(ORIGIN, outcome="rules", status=200, body=b"User-agent: *\nDisallow: /\n")
    old = cache._entry_path(ORIGIN).read_bytes()
    with pytest.raises(ValueError, match="too_large"):
        request_once(client, policy, cache, output)
    assert cache._entry_path(ORIGIN).read_bytes() == old


def test_transport_error_has_no_retry_or_timestamp_change(tmp_path):
    client, policy, cache, output = setup(tmp_path, broken=True)
    with pytest.raises(OSError):
        request_once(client, policy, cache, output)
    assert len(client.calls) == 1 and cache.get(ORIGIN) is None


def test_silent_cache_write_failure_is_detected(tmp_path):
    client, policy, cache, output = setup(tmp_path)
    cache.put = lambda *a, **k: None
    with pytest.raises(ValueError, match="not_saved"):
        request_once(client, policy, cache, output)


def test_silent_put_cannot_reuse_fresh_identical_old_body(tmp_path):
    import datetime as dt

    client, policy, cache, output = setup(tmp_path)
    cache._clock = lambda: dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    cache.put(ORIGIN, outcome="rules", status=200, body=client.body)
    cache.put = lambda *a, **k: None
    with pytest.raises(ValueError, match="not_updated_this_attempt"):
        request_once(client, policy, cache, output)


def test_lock_conflict_refuses_without_wait(tmp_path):
    with exclusive_cache_lock(tmp_path):
        with pytest.raises(BlockingIOError):
            with exclusive_cache_lock(tmp_path):
                pytest.fail("must not acquire a second file lock")


def test_lock_symlink_is_rejected(tmp_path):
    import hashlib

    path = tmp_path / (hashlib.sha256(ORIGIN.encode()).hexdigest()[:20] + ".lock")
    path.symlink_to(tmp_path / "other")
    with pytest.raises(OSError):
        with exclusive_cache_lock(tmp_path):
            pytest.fail("must not follow lock symlink")
