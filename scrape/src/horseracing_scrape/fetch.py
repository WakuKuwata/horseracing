"""Polite HTTP fetch layer (R7): robots.txt, per-domain rate limit, file cache, UA, backoff.

The network client, sleep and clock are injectable so the politeness behavior is unit-testable
without real network or wall-clock (FR-001). FixtureFetcher serves saved HTML for network-free
parser/pipeline tests.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

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


#: URL PATHS that are never archived, matched on the normalized path so no query/type variant can
#: slip past. The odds API is excluded because a timestamped archive of it would be an odds time
#: series through the back door, and the constitution stores odds as a single latest value with no
#: history. Every win/exotic quote variant shares this one path, differing only by `type=`.
#: Result pages carry settled dividends (a final fact, not a series) and are fine.
ARCHIVE_DENY_PATHS: frozenset[str] = frozenset({"/api/api_get_jra_odds.html"})


def archive_allowed(url: str) -> bool:
    """Whether a fetched URL may be retained in the page archive (constitution V)."""
    return urlsplit(url).path not in ARCHIVE_DENY_PATHS


def _refused(resp, url: str) -> FetchRefused:
    return FetchRefused(
        resp.status_code,
        url,
        retry_after_s=_retry_after_s(resp),
    )


class HttpFetcher:
    """Production fetcher. ``client`` must expose ``get(url) -> response`` with ``status_code``
    and ``text`` (httpx.Client by default). ``sleep``/``clock`` are injectable for tests."""

    def __init__(
        self,
        *,
        user_agent: str,
        min_interval_s: float = 1.0,
        cache_dir: str | Path | None = None,
        archive_dir: str | Path | None = None,
        max_retries: int = 3,
        client=None,
        sleep=time.sleep,
        clock=time.monotonic,
        respect_robots: bool = True,
        pre_request: PreRequest | None = None,
    ):
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.archive_dir = Path(archive_dir) if archive_dir else None
        if self.cache_dir is not None and self.archive_dir is not None:
            # The two are opposites: cache_dir SERVES saved bytes instead of fetching (fine for a
            # one-off backfill, fatal for the daily job — a result page fetched while the race was
            # still pending would be served forever and the race would never get its results).
            # archive_dir always fetches and keeps a copy. Allowing both invites exactly that
            # confusion, so refuse it rather than silently pick one.
            raise ValueError("cache_dir and archive_dir are mutually exclusive")
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._respect_robots = respect_robots
        self._pre_request = pre_request
        self._last_fetch: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

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
        text = self._fetch_with_backoff(url)  # archives the accepted 200 internally
        if use_cache:
            self._cache_write(url, text)
        return text

    # --- robots -------------------------------------------------------------
    def _robot_allows(self, url: str) -> bool:
        domain = _domain(url)
        if domain not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                robots_url = f"{domain}/robots.txt"
                self._before_request(robots_url)
                resp = self._client.get(robots_url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                elif resp.status_code == 429:
                    raise _refused(resp, robots_url)
                else:
                    rp = None  # no robots -> allow
            except FetchRefused:
                raise
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
                    # Archive the ACCEPTED response only, and do it here where the raw bytes are
                    # still in hand: _resolve_text may decode with errors="replace", so archiving
                    # the decoded string would bake a lossy transcription into the permanent copy.
                    # Being inside the retry loop also means a 500-then-200 archives only the 200.
                    self._archive_write(url, resp)
                    return _resolve_text(resp)
                if resp.status_code in REFUSAL_STATUSES:
                    raise _refused(resp, url)
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

    # --- archive --------------------------------------------------------------
    def _archive_write(self, url: str, resp) -> None:
        """Keep a gzipped copy of an accepted response. Write-only and append-only.

        This is NOT a cache: nothing reads it back during a run, so it cannot make the pipeline
        serve stale content. Each fetch lands in its own timestamped file, so a results page
        fetched while the race was still pending stays distinguishable from the settled one
        fetched later — "is this race final?" is answered from race_results in the DB, never from
        a file here, and never from "newest wins".

        RAW BYTES are stored, not decoded text: db.netkeiba is EUC-JP and ``_resolve_text`` falls
        back to ``errors="replace"``, so archiving the decoded string would bake a lossy
        transcription into the copy that exists precisely to be re-parsed later.

        Failures are swallowed: a full disk must not take down a scrape run.
        """
        if self.archive_dir is None or not archive_allowed(url):
            return
        try:
            raw = getattr(resp, "content", None)
            if raw is None:  # test double without .content
                raw = (resp.text or "").encode("utf-8")
            if not raw:
                return
            parts = urlsplit(url)
            host = parts.netloc or "unknown"
            key = hashlib.sha256(url.encode()).hexdigest()[:16]
            folder = self.archive_dir / host / key
            folder.mkdir(parents=True, exist_ok=True)
            marker = folder / "url.txt"  # the hash alone is not reversible
            if not marker.exists():
                marker.write_text(url + "\n", encoding="utf-8")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            # Exclusive create with a collision suffix: two threads sharing a microsecond must
            # not silently drop one observation, and nothing may overwrite an existing one.
            for n in range(100):
                path = folder / (f"{stamp}.html.gz" if n == 0 else f"{stamp}-{n}.html.gz")
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb") as gz:
                    gz.write(raw)
                return
        except Exception:  # noqa: BLE001 — archiving is best-effort, never fatal
            return
