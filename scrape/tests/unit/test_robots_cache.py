"""093: durable robots.txt cache.

The point of the cache is budget — at 1 request/minute, re-fetching robots per fetcher can eat a
third to a half of the daily allowance. But the DANGEROUS failure is not a wasted request: it is
caching an "allow" that came from a transient failure, which would keep us fetching paths robots
forbids for a whole TTL. Most of this file is about what must NOT be stored.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from horseracing_scrape.fetch import FetchRefused, HttpFetcher, RobotsDisallowed
from horseracing_scrape.robots_cache import ABSENT, RULES, RobotsCache

_DISALLOW = b"User-agent: *\nDisallow: /race/\n"
_ALLOW_ALL = b"User-agent: *\nDisallow:\n"
ORIGIN = "https://race.netkeiba.com"
TARGET = f"{ORIGIN}/race/result.html?race_id=1"


class _Resp:
    def __init__(self, body: bytes = b"", status: int = 200):
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.status_code = status
        self.headers: dict[str, str] = {}


class _Client:
    """Serves a scripted robots response and counts requests by kind."""

    def __init__(self, robots: _Resp | Exception, page: _Resp | None = None):
        self._robots, self._page = robots, page or _Resp(b"<html>page</html>")
        self.robots_calls = 0
        self.page_calls = 0

    def get(self, url, **kw):
        if url.endswith("/robots.txt"):
            self.robots_calls += 1
            if isinstance(self._robots, Exception):
                raise self._robots
            return self._robots
        self.page_calls += 1
        return self._page


def _fetcher(cache: RobotsCache | None, client) -> HttpFetcher:
    return HttpFetcher(
        user_agent="test", min_interval_s=0.0, sleep=lambda _s: None,
        client=client, robots_cache_store=cache,
    )


def _cache(tmp_path: Path, **kw) -> RobotsCache:
    return RobotsCache(tmp_path / "robots", **kw)


# --- the cache does its job -------------------------------------------------

def test_two_fetchers_sharing_a_directory_fetch_robots_once(tmp_path):
    """The whole point: a per-iteration fetcher must not re-buy robots every time."""
    cache = _cache(tmp_path)
    c1, c2 = _Client(_Resp(_ALLOW_ALL)), _Client(_Resp(_ALLOW_ALL))

    _fetcher(cache, c1).get(TARGET)
    _fetcher(cache, c2).get(TARGET)

    assert c1.robots_calls == 1
    assert c2.robots_calls == 0, "the second fetcher must read the durable entry, not the network"


def test_a_cached_disallow_still_blocks(tmp_path):
    """Caching must not weaken enforcement."""
    cache = _cache(tmp_path)
    first = _Client(_Resp(_DISALLOW))
    with pytest.raises(RobotsDisallowed):
        _fetcher(cache, first).get(TARGET)

    second = _Client(_Resp(_ALLOW_ALL))  # would allow if it were re-fetched
    with pytest.raises(RobotsDisallowed):
        _fetcher(cache, second).get(TARGET)
    assert second.robots_calls == 0
    assert second.page_calls == 0, "a disallowed target must never reach the network"


@pytest.mark.parametrize("status", [404, 410])
def test_absent_robots_is_durable(tmp_path, status):
    """"This site has no robots" is a fact about the site, so it is cacheable."""
    cache = _cache(tmp_path)
    c1 = _Client(_Resp(b"", status))
    _fetcher(cache, c1).get(TARGET)
    assert cache.get(ORIGIN).outcome == ABSENT

    c2 = _Client(_Resp(b"", status))
    _fetcher(cache, c2).get(TARGET)
    assert c2.robots_calls == 0


# --- what must NOT be cached ------------------------------------------------

@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_are_never_cached(tmp_path, status):
    """A 5xx says nothing about the site's policy. Storing the resulting 'allow' would keep us
    fetching forbidden paths for a whole TTL."""
    cache = _cache(tmp_path)
    c1 = _Client(_Resp(b"", status))
    _fetcher(cache, c1).get(TARGET)

    assert cache.get(ORIGIN) is None, "a transient failure must not become policy"
    c2 = _Client(_Resp(_DISALLOW))
    with pytest.raises(RobotsDisallowed):
        _fetcher(cache, c2).get(TARGET)
    assert c2.robots_calls == 1, "the next caller must retry robots, not inherit the failure"


def test_network_exception_is_never_cached(tmp_path):
    cache = _cache(tmp_path)
    _fetcher(cache, _Client(ConnectionError("dropped"))).get(TARGET)
    assert cache.get(ORIGIN) is None


@pytest.mark.parametrize("status", [400, 403, 429])
def test_refusals_are_never_cached(tmp_path, status):
    """400 is netkeiba's real block status; none of these describe the robots policy."""
    cache = _cache(tmp_path)
    client = _Client(_Resp(b"", status))
    if status == 429:
        with pytest.raises(FetchRefused):
            _fetcher(cache, client).get(TARGET)
    else:
        _fetcher(cache, client).get(TARGET)
    assert cache.get(ORIGIN) is None


def test_a_write_failure_cannot_turn_disallow_into_allow(tmp_path):
    """The shape that would be worst: robots said no, the disk was full, and the error handler
    swallowed the decision along with the write."""
    blocked = tmp_path / "robots"
    blocked.write_text("not a directory")  # mkdir underneath will raise
    cache = RobotsCache(blocked)
    with pytest.raises(RobotsDisallowed):
        _fetcher(cache, _Client(_Resp(_DISALLOW))).get(TARGET)


# --- freshness / integrity --------------------------------------------------

def test_entries_expire(tmp_path):
    now = {"t": datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)}
    cache = _cache(tmp_path, ttl_s=3600, clock=lambda: now["t"])
    _fetcher(cache, _Client(_Resp(_ALLOW_ALL))).get(TARGET)
    assert cache.get(ORIGIN) is not None

    now["t"] += datetime.timedelta(seconds=3601)
    assert cache.get(ORIGIN) is None, "a stale entry must not be served"


def test_future_dated_entry_is_treated_as_stale(tmp_path):
    """Clock skew must not produce an entry that is fresh forever."""
    now = {"t": datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)}
    cache = _cache(tmp_path, clock=lambda: now["t"])
    cache.put(ORIGIN, outcome=RULES, status=200, body=_ALLOW_ALL)
    now["t"] -= datetime.timedelta(days=2)
    assert cache.get(ORIGIN) is None


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(schema_version=999), id="wrong-version"),
        pytest.param(lambda d: d.update(origin="https://evil.example"), id="foreign-origin"),
        pytest.param(lambda d: d.update(outcome="whatever"), id="unknown-outcome"),
        pytest.param(lambda d: d.update(body="!!!not-base64!!!"), id="corrupt-body"),
    ],
)
def test_untrustworthy_entries_are_a_miss(tmp_path, mutate):
    cache = _cache(tmp_path)
    cache.put(ORIGIN, outcome=RULES, status=200, body=_ALLOW_ALL)
    path = next((tmp_path / "robots").glob("*.json"))
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc))
    assert cache.get(ORIGIN) is None


def test_truncated_file_is_a_miss_not_a_crash(tmp_path):
    cache = _cache(tmp_path)
    cache.put(ORIGIN, outcome=RULES, status=200, body=_ALLOW_ALL)
    next((tmp_path / "robots").glob("*.json")).write_text('{"schema_version": 1, "orig')
    assert cache.get(ORIGIN) is None


def test_refusing_to_cache_a_non_site_outcome_is_programmer_error(tmp_path):
    with pytest.raises(ValueError, match="refusing to cache"):
        _cache(tmp_path).put(ORIGIN, outcome="transient", status=500)


# --- scope ------------------------------------------------------------------

def test_origins_are_separate_including_port(tmp_path):
    cache = _cache(tmp_path)
    cache.put(ORIGIN, outcome=RULES, status=200, body=_DISALLOW)
    assert cache.get("https://db.netkeiba.com") is None
    assert cache.get("https://race.netkeiba.com:8443") is None


def test_page_cache_disabled_still_uses_the_robots_cache(tmp_path):
    """Odds are fetched with use_cache=False (constitution V). That must not also throw away the
    robots cache — the volatile-content rule and the robots policy are different concerns."""
    cache = _cache(tmp_path)
    _fetcher(cache, _Client(_Resp(_ALLOW_ALL))).get(TARGET)
    c2 = _Client(_Resp(_ALLOW_ALL))
    _fetcher(cache, c2).get(TARGET, use_cache=False)
    assert c2.robots_calls == 0


def test_without_a_cache_behaviour_is_unchanged(tmp_path):
    """Default-off keeps every existing fetcher isolated from any shared directory."""
    client = _Client(_Resp(_ALLOW_ALL))
    f = _fetcher(None, client)
    f.get(TARGET)
    f.get(TARGET)
    assert client.robots_calls == 1  # in-process memo only
    assert not (tmp_path / "robots").exists()


# --- production wiring ------------------------------------------------------

def test_shared_cache_resolves_to_one_absolute_path(monkeypatch, tmp_path):
    """Every entry point must land on the SAME directory. The capture subprocess runs with
    cwd=live/, so a relative path would hand capture its own empty cache — the one caller whose
    deadline the cache exists to make satisfiable."""
    from horseracing_scrape.robots_cache import ROBOTS_CACHE_ENV, shared_cache

    monkeypatch.delenv(ROBOTS_CACHE_ENV, raising=False)
    assert shared_cache().directory.is_absolute()

    monkeypatch.setenv(ROBOTS_CACHE_ENV, str(tmp_path / "shared"))
    assert shared_cache().directory == (tmp_path / "shared").resolve()


def test_shared_cache_can_be_turned_off(monkeypatch):
    from horseracing_scrape.robots_cache import ROBOTS_CACHE_ENV, shared_cache

    monkeypatch.setenv(ROBOTS_CACHE_ENV, "")
    assert shared_cache() is None
