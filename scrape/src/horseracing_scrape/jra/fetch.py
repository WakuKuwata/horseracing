"""``JraFetcher`` — the JRA official site adapter (Feature 176, contracts/fetch-and-parse.md §1).

A separate class from ``HttpFetcher`` on purpose (research D2): the two routes differ in the request
method (POST ``cname``), the decoding (cp932 strict, never a replacement character), the response
classification (an HTTP 200 error screen and a 3xx to ``error013`` are PARAMETER errors, not
refusals) and the archive policy (odds pages are never retained). What IS shared is the politeness
machinery — ``RequestPoliteness.pre_request`` / ``record_refusal`` and the durable robots cache —
so this cannot become the seventh route that bypasses the machine-wide request budget (093).

Every HTTP send (robots, GET, POST) goes through the same four steps: capture-window guard →
shared slot reservation → capture-window guard again → send. ``requests_sent`` counts them all.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .. import robots_cache
from ..fetch import FetchError, FetchRefused, RobotsDisallowed, _retry_after_s
from ..politeness import RequestPoliteness
from .errors import JraDecodeError, JraParameterError
from .models import KIND_ROBOTS, JraResponse, archivable

USER_AGENT = "horseracing-scrape/0.1 (personal use; contact via repo)"
TIMEOUT_S = 20.0

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?\s*([\w-]+)""", re.IGNORECASE)
_SJIS_ALIASES = frozenset(
    {"shift_jis", "shift-jis", "sjis", "x-sjis", "windows-31j", "ms932", "cp932"}
)
#: The HTTP-200 "パラメータエラー" screen (title) and the error013 redirect target.
_PARAM_ERROR_TITLE_RE = re.compile(r"<title>\s*パラメータエラー")
_ERROR013 = "error013"
#: Refusals that install a shared cooldown (RequestPoliteness.record_refusal decides the length).
_REFUSAL_STATUSES = frozenset({403, 429})
_GET_RETRIES = 2  # one retry for an idempotent GET on 5xx / connection error; POST never retries

CaptureGuard = Callable[[], object | None]


class CaptureWindowRefused(FetchRefused):
    """A near-post capture hold is open (174): the JRA route stops at the next hop (FR-004).

    Defined here (scrape) so ops can re-export it; shares the ``FetchRefused`` base so callers
    that only know refusals still stop."""

    def __init__(self, url: str, detail: object = None):
        self.detail = detail
        super().__init__(408, url)
        self.args = (f"request skipped (capture_window) for {url}",)


def _decode(raw: bytes, content_type: str | None) -> str:
    """Header charset → meta charset → cp932 default (the site never declares one in the header).

    Strict: a byte that is not cp932 raises ``JraDecodeError`` and the response is discarded."""
    enc = None
    if content_type and "charset=" in content_type.lower():
        enc = content_type.lower().split("charset=", 1)[1].split(";")[0].strip().strip('"')
    if enc is None:
        m = _META_CHARSET_RE.search(raw[:4096])
        if m:
            enc = m.group(1).decode("ascii", "ignore").strip().lower()
    if enc is None or enc in _SJIS_ALIASES:
        enc = "cp932"
    try:
        return raw.decode(enc, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        raise JraDecodeError(f"cannot decode response strictly as {enc}: {exc}") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class JraFetcher:
    """Polite POST/GET client for www.jra.go.jp. See module docstring for the contract."""

    def __init__(
        self,
        *,
        policy: RequestPoliteness,
        capture_guard: CaptureGuard | None = None,
        archive_dir: str | Path | None = None,
        client: httpx.Client | None = None,
        robots_cache_store: robots_cache.RobotsCache | None = None,
        respect_robots: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if policy is None:
            raise ValueError("JraFetcher requires a RequestPoliteness (shared slot) — no bypass")
        self._policy = policy
        self._guard = capture_guard
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self._client = client or httpx.Client(
            follow_redirects=False, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}
        )
        self._robots_cache = robots_cache_store
        self._respect_robots = respect_robots
        self._clock = clock
        self._sleep = sleep
        self._robots: dict[str, RobotFileParser | None] = {}
        self.requests_sent = 0

    # --- public ---------------------------------------------------------------
    def get(self, url: str, *, kind: str) -> JraResponse:
        self._robots_check(url)
        last: Exception | None = None
        for attempt in range(_GET_RETRIES):
            try:
                resp = self._send(url, kind=kind, method="GET")
                return self._classify(url, None, resp, kind)
            except (FetchRefused, JraParameterError, JraDecodeError):
                raise
            except FetchError as exc:
                last = exc
                if attempt + 1 < _GET_RETRIES:
                    self._sleep(1.0)
        raise FetchError(f"failed to GET {url}: {last}")

    def post(self, url: str, cname: str, *, kind: str) -> JraResponse:
        """One POST, never retried (a retried bad cname is the same bad cname)."""
        self._robots_check(url)
        resp = self._send(url, kind=kind, method="POST", data={"cname": cname})
        return self._classify(url, cname, resp, kind)

    # --- the four steps before every send ---------------------------------------
    def _before_send(self, url: str) -> None:
        self._guard_check(url)
        self._policy.pre_request(url)
        self._guard_check(url)

    def _guard_check(self, url: str) -> None:
        if self._guard is None:
            return
        window = self._guard()
        if window is not None:
            raise CaptureWindowRefused(url, window)

    def _send(self, url: str, *, kind: str, method: str, data: dict | None = None):
        self._before_send(url)
        self.requests_sent += 1
        try:
            if method == "POST":
                return self._client.post(url, data=data)
            return self._client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"{method} {url} failed: {exc}") from exc

    # --- classification (contracts/fetch-and-parse.md §1) -------------------------
    def _classify(self, url: str, cname: str | None, resp, kind: str) -> JraResponse:
        status = resp.status_code
        if status in _REFUSAL_STATUSES or (status == 503 and _retry_after_s(resp) is not None):
            refusal = FetchRefused(status, url, retry_after_s=_retry_after_s(resp))
            try:
                self._policy.record_refusal(refusal, request_url=url)
            except Exception:  # noqa: BLE001 — reporting must not mask the refusal
                pass
            raise refusal
        if 300 <= status < 400:
            location = resp.headers.get("Location", "") if hasattr(resp, "headers") else ""
            if _ERROR013 in location:
                raise JraParameterError(f"redirected to {location}", url=url, status=status)
            raise JraParameterError(f"unexpected redirect to {location!r}", url=url, status=status)
        if status == 400:
            raise JraParameterError("HTTP 400 (parameter error)", url=url, status=400)
        if status != 200:
            raise FetchError(f"HTTP {status} for {url}")
        raw: bytes = resp.content
        text = _decode(raw, resp.headers.get("Content-Type") if hasattr(resp, "headers") else None)
        if _PARAM_ERROR_TITLE_RE.search(text[:4096]):
            raise JraParameterError("HTTP 200 parameter-error screen", url=url, status=200)
        fetched_at = self._clock()
        self._archive(url, cname, raw, kind)
        return JraResponse(
            url=url,
            cname_sha256=_sha(cname.encode()) if cname is not None else None,
            status=status,
            raw=raw,
            text=text,
            fetched_at=fetched_at,
            kind=kind,
            raw_sha256=_sha(raw),
        )

    # --- robots ---------------------------------------------------------------------
    def _robots_check(self, url: str) -> None:
        if not self._respect_robots:
            return
        origin = robots_cache.origin_of(url)
        if origin in self._robots:
            rp = self._robots[origin]
        else:
            rp = self._load_robots(origin)
            self._robots[origin] = rp
        if rp is not None and not rp.can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        if self._robots_cache is not None:
            entry = self._robots_cache.get(origin)
            if entry is not None:
                if entry.outcome == robots_cache.ABSENT:
                    return None
                rp = RobotFileParser()
                rp.parse(entry.body.decode("utf-8", errors="replace").splitlines())
                return rp
        robots_url = f"{origin}/robots.txt"
        resp = self._send(robots_url, kind=KIND_ROBOTS, method="GET")
        status = resp.status_code
        if status in _REFUSAL_STATUSES:
            refusal = FetchRefused(status, robots_url, retry_after_s=_retry_after_s(resp))
            try:
                self._policy.record_refusal(refusal, request_url=robots_url)
            except Exception:  # noqa: BLE001
                pass
            raise refusal
        if status == 200:
            body = resp.content
            if self._robots_cache is not None:
                self._robots_cache.put(origin, outcome=robots_cache.RULES, status=200, body=body)
            rp = RobotFileParser()
            rp.parse(body.decode("utf-8", errors="replace").splitlines())
            return rp
        if status in (404, 410):
            if self._robots_cache is not None:
                self._robots_cache.put(origin, outcome=robots_cache.ABSENT, status=status, body=b"")
            return None
        return None  # transient: allow this run, cache nothing (same as HttpFetcher)

    # --- archive (kind allow-list) ----------------------------------------------------
    def _archive(self, url: str, cname: str | None, raw: bytes, kind: str) -> None:
        if self.archive_dir is None or not archivable(kind):
            return
        try:
            host = urlsplit(url).netloc or "unknown"
            key_src = url + "\n" + (cname or "")
            key = _sha(key_src.encode())[:16]
            folder = self.archive_dir / host / key
            folder.mkdir(parents=True, exist_ok=True)
            marker = folder / "url.txt"
            if not marker.exists():
                cname_line = f"cname_sha256={_sha(cname.encode())}\n" if cname else ""
                marker.write_text(f"{url}\n{cname_line}", encoding="utf-8")
            stamp = self._clock().strftime("%Y%m%dT%H%M%S%fZ")
            path = folder / f"{stamp}.html"
            n = 0
            while path.exists():
                n += 1
                path = folder / f"{stamp}-{n}.html"
            path.write_bytes(raw)
        except Exception:  # noqa: BLE001 — archiving is best-effort
            return
