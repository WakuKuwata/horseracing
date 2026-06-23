"""Polite HTTP fetch layer (R7): robots.txt, per-domain rate limit, file cache, UA, backoff.

The network client, sleep and clock are injectable so the politeness behavior is unit-testable
without real network or wall-clock (FR-001). FixtureFetcher serves saved HTML for network-free
parser/pipeline tests.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class FetchError(RuntimeError):
    pass


class RobotsDisallowed(FetchError):
    pass


@runtime_checkable
class PoliteFetcher(Protocol):
    def get(self, url: str) -> str: ...


class FixtureFetcher:
    """Test fetcher: returns saved HTML for known URLs (network-free)."""

    def __init__(self, pages: dict[str, str]):
        self._pages = dict(pages)

    def get(self, url: str) -> str:
        if url not in self._pages:
            raise FetchError(f"no fixture for {url}")
        return self._pages[url]


def _domain(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class HttpFetcher:
    """Production fetcher. ``client`` must expose ``get(url) -> response`` with ``status_code``
    and ``text`` (httpx.Client by default). ``sleep``/``clock`` are injectable for tests."""

    def __init__(
        self,
        *,
        user_agent: str,
        min_interval_s: float = 1.0,
        cache_dir: str | Path | None = None,
        max_retries: int = 3,
        client=None,
        sleep=time.sleep,
        clock=time.monotonic,
        respect_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._respect_robots = respect_robots
        self._last_fetch: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    # --- public -------------------------------------------------------------
    def get(self, url: str) -> str:
        cached = self._cache_read(url)
        if cached is not None:
            return cached
        if self._respect_robots and not self._robot_allows(url):
            raise RobotsDisallowed(url)
        self._rate_limit(_domain(url))
        text = self._fetch_with_backoff(url)
        self._cache_write(url, text)
        return text

    # --- robots -------------------------------------------------------------
    def _robot_allows(self, url: str) -> bool:
        domain = _domain(url)
        if domain not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                resp = self._client.get(f"{domain}/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None  # no robots -> allow
            except Exception:
                rp = None
            self._robots[domain] = rp
        rp = self._robots[domain]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # --- rate limit ---------------------------------------------------------
    def _rate_limit(self, domain: str) -> None:
        last = self._last_fetch.get(domain)
        now = self._clock()
        if last is not None:
            wait = self.min_interval_s - (now - last)
            if wait > 0:
                self._sleep(wait)
        self._last_fetch[domain] = self._clock()

    # --- fetch + backoff ----------------------------------------------------
    def _fetch_with_backoff(self, url: str) -> str:
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                last_err = FetchError(f"HTTP {resp.status_code} for {url}")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            if attempt < self.max_retries - 1:
                self._sleep(delay)
                delay *= 2  # exponential backoff
        raise FetchError(f"failed to fetch {url}: {last_err}")

    # --- cache --------------------------------------------------------------
    def _cache_file(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{key}.html"

    def _cache_read(self, url: str) -> str | None:
        path = self._cache_file(url)
        if path is not None and path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _cache_write(self, url: str, text: str) -> None:
        path = self._cache_file(url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
