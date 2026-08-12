"""Polite HTTP fetch layer (R7): robots.txt, per-domain rate limit, file cache, UA, backoff.

The network client, sleep and clock are injectable so the politeness behavior is unit-testable
without real network or wall-clock (FR-001). FixtureFetcher serves saved HTML for network-free
parser/pipeline tests.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from . import robots_cache


class _Missing:
    """Distinguishes "no cached robots" from a cached "this site has no robots" (both falsy)."""


_MISSING = _Missing()
#: TTL for the in-process robots cache when no durable cache is configured.
_L1_TTL_S = 24 * 60 * 60

_META_CHARSET_RE = re.compile(rb"""charset=["']?\s*([\w-]+)""", re.IGNORECASE)
_MAX_RETRY_AFTER_S = 6 * 60 * 60

PreRequest = Callable[[str], None]


def _resolve_text(resp) -> str:
    """Decode a response honoring the page charset.

    netkeiba is mixed-encoding: race.netkeiba.com is UTF-8, but db.netkeiba.com is **EUC-JP and
    sends no charset in the Content-Type header** — only a `<meta charset>` in the body. httpx's
    content-sniffing then mis-decodes it (mojibake). So: header charset wins (httpx already decoded
    right); otherwise sniff the meta charset from the raw bytes and decode explicitly; otherwise
    fall back to httpx's text. Test doubles without `.content` just use `.text`."""
    content = getattr(resp, "content", None)
    if content is None:  # test double / non-httpx client
        return resp.text
    if getattr(resp, "charset_encoding", None):  # charset in Content-Type header → trust httpx
        return resp.text
    m = _META_CHARSET_RE.search(content[:2048])
    if m:
        enc = m.group(1).decode("ascii", "ignore").strip().lower()
        try:
            return content.decode(enc, errors="replace")
        except LookupError:
            pass
    return resp.text


class FetchError(RuntimeError):
    pass


class FetchRefused(FetchError):
    """The source refused or rate-limited a request; callers must not retry it."""

    def __init__(
        self,
        status_code: int,
        url: str | None = None,
        *,
        retry_after_s: float | None = None,
    ):
        self.status_code = status_code
        self.url = url
        self.retry_after_s = retry_after_s
        target = f" for {url}" if url is not None else ""
        retry = f" (retry after {retry_after_s:g}s)" if retry_after_s is not None else ""
        super().__init__(f"HTTP {status_code} refused{target}{retry}")


class RobotsDisallowed(FetchError):
    pass


@runtime_checkable
class PoliteFetcher(Protocol):
    def get(self, url: str, *, use_cache: bool = True) -> str: ...


class FixtureFetcher:
    """Test fetcher: returns saved HTML for known URLs (network-free)."""

    def __init__(self, pages: dict[str, str]):
        self._pages = dict(pages)

    def get(self, url: str, *, use_cache: bool = True) -> str:
        if url not in self._pages:
            raise FetchError(f"no fixture for {url}")
        return self._pages[url]


def _domain(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _retry_after_s(resp) -> float | None:
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None

    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(raw))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None

    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_S)


#: Statuses that mean "stop", not "try again". 403/429 are the documented ones, but netkeiba also
#: answers a sustained-load block with a bare 400 and an EMPTY body — including on plain HTML pages
#: that had just served fine. Treating that as an ordinary error made a bulk pass grind through its
#: whole list one request at a time while every single one was being refused; the retry/backoff and
#: cooldown that exist for exactly this situation never engaged.
REFUSAL_STATUSES: frozenset[int] = frozenset({400, 403, 429})


def _refused(resp, url: str) -> FetchRefused:
    return FetchRefused(
        resp.status_code,
        url,
        retry_after_s=_retry_after_s(resp),
    )


#: Called with the refusal the moment the source declines, before it propagates. The shared
#: politeness policy uses it to install a cooldown that every OTHER process observes — without
#: it, only the process that got refused would back off and the rest keep hammering a source
#: that is already blocking us.
OnRefusal = Callable[[FetchRefused], None]


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
        pre_request: PreRequest | None = None,
        on_refusal: OnRefusal | None = None,
        robots_cache_store: robots_cache.RobotsCache | None = None,
    ):
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._respect_robots = respect_robots
        self._pre_request = pre_request
        self._on_refusal = on_refusal
        self._last_fetch: dict[str, float] = {}
        #: origin -> (parser|None, stored_at). Timestamped so a long-lived fetcher cannot
        #: hold an 'allow' forever after the durable entry has expired.
        self._robots: dict[str, tuple] = {}
        #: Durable cross-process robots store. None = in-process only (the old behaviour), which
        #: keeps every existing unit test isolated from any shared directory.
        self._robots_cache = robots_cache_store

    # --- public -------------------------------------------------------------
    def get(self, url: str, *, use_cache: bool = True) -> str:
        # use_cache=False for volatile data (odds, constitution V single-latest): never serve or
        # write a stale cached value.
        if use_cache:
            cached = self._cache_read(url)
            if cached is not None:
                return cached
        if self._respect_robots and not self._robot_allows(url):
            raise RobotsDisallowed(url)
        self._rate_limit(_domain(url))
        text = self._fetch_with_backoff(url)
        if use_cache:
            self._cache_write(url, text)
        return text

    # --- robots -------------------------------------------------------------
    def _parse_robots(self, body: bytes) -> RobotFileParser:
        rp = RobotFileParser()
        rp.parse(body.decode("utf-8", errors="replace").splitlines())
        return rp

    def _robots_from_cache(self, origin: str) -> RobotFileParser | None | _Missing:
        """Durable-cache lookup. Returns a parser, None (site has no robots), or _MISSING."""
        if self._robots_cache is None:
            return _MISSING
        entry = self._robots_cache.get(origin)
        if entry is None:
            return _MISSING
        return None if entry.outcome == robots_cache.ABSENT else self._parse_robots(entry.body)

    def _robot_allows(self, url: str) -> bool:
        origin = _domain(url)
        cached = self._l1_robots(origin)
        if cached is not _MISSING:
            return True if cached is None else cached.can_fetch(self.user_agent, url)

        from_disk = self._robots_from_cache(origin)
        if from_disk is not _MISSING:
            self._remember_robots(origin, from_disk)
            return True if from_disk is None else from_disk.can_fetch(self.user_agent, url)

        # Cache miss: one fetch per origin, serialized across threads and processes so a TTL
        # boundary does not make four callers each spend a request slot on the same file.
        ctx = (
            self._robots_cache.refresh_lock(origin)
            if self._robots_cache is not None
            else contextlib.nullcontext(False)
        )
        with ctx as got_lock:
            if got_lock:  # another holder may have just filled it while we waited
                again = self._robots_from_cache(origin)
                if again is not _MISSING:
                    self._remember_robots(origin, again)
                    return True if again is None else again.can_fetch(self.user_agent, url)
            rp, cacheable = self._fetch_robots(origin)

        # Only site-level outcomes reach the durable store. A 5xx or a dropped connection says
        # nothing about the site's policy, and freezing it as "allow" would keep us fetching
        # paths robots forbids for a whole TTL.
        if cacheable is not None and self._robots_cache is not None:
            outcome, status, body = cacheable
            with contextlib.suppress(Exception):  # write failure must not change the decision
                self._robots_cache.put(origin, outcome=outcome, status=status, body=body)
        if cacheable is not None:
            self._remember_robots(origin, rp)
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    def _fetch_robots(self, origin: str):
        """Fetch robots for ``origin``. Returns ``(parser_or_None, cacheable_or_None)``."""
        robots_url = f"{origin}/robots.txt"
        try:
            self._before_request(robots_url)
            resp = self._client.get(robots_url)
            if resp.status_code == 200:
                body = getattr(resp, "content", None)
                if body is None:
                    body = (resp.text or "").encode("utf-8")
                return self._parse_robots(body), (robots_cache.RULES, 200, body)
            if resp.status_code == 429:
                raise self._refuse(resp, robots_url)
            if resp.status_code in (404, 410):
                # A site that answers "no such file" has no robots policy. That IS durable.
                return None, (robots_cache.ABSENT, resp.status_code, b"")
            return None, None  # transient/refusal — allow this request, cache nothing
        except FetchRefused:
            raise
        except Exception:  # noqa: BLE001 — unreachable robots keeps the caller's prior behaviour
            return None, None

    def _l1_robots(self, origin: str):
        """In-process cache, expired against the same TTL as the durable store."""
        hit = self._robots.get(origin)
        if hit is None:
            return _MISSING
        rp, stored_at = hit
        ttl = self._robots_cache.ttl_s if self._robots_cache is not None else _L1_TTL_S
        if (time.time() - stored_at) >= ttl:
            self._robots.pop(origin, None)
            return _MISSING
        return rp

    def _remember_robots(self, origin: str, rp: RobotFileParser | None) -> None:
        self._robots[origin] = (rp, time.time())

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
    def _refuse(self, resp, url: str) -> FetchRefused:
        """Build the refusal AND notify the policy. Reporting must not mask the refusal, so a
        broken hook is swallowed — the caller still sees the source say no."""
        exc = _refused(resp, url)
        if self._on_refusal is not None:
            try:
                self._on_refusal(exc)
            except Exception:  # noqa: BLE001
                pass
        return exc

    def _before_request(self, url: str) -> None:
        if self._pre_request is not None:
            self._pre_request(url)

    def _fetch_with_backoff(self, url: str) -> str:
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self._before_request(url)
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return _resolve_text(resp)
                if resp.status_code in REFUSAL_STATUSES:
                    raise self._refuse(resp, url)
                last_err = FetchError(f"HTTP {resp.status_code} for {url}")
            except FetchRefused:
                raise
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
