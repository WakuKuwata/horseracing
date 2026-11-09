"""Member-site fetcher for JRDB deliveries (Feature 178, T029; contracts/files-and-ids.md §1.1).

This is the ONLY module that spells the member host path (the fixture-manifest test enforces it).
Everything the fetcher logs or raises names a file by its basename and the URL by a sha, never the
member URL itself; the password never appears anywhere (FR-022, 会員規約 第 5 条).

Contract (tasks T024):

* one request per file — no retries, no redirects followed;
* a fixed minimum interval between sends (``HORSERACING_JRDB_MIN_INTERVAL_S``, default 5 s,
  floor 2 s) — the JRDB host is separate from netkeiba/JRA, so it does not use the shared slot
  (different host, different contract) but still never bursts;
* ``HEAD`` first when a previous version is known: same ``Last-Modified`` and size → no ``GET``;
* 401/403 → :class:`FetchRefused` at once (credentials or rule problem; nothing else is sent),
  429/503 → :class:`FetchRefused` with the Retry-After, 404 → :class:`JrdbNotFound`;
* archive (back-number) requests are refused by the caller's blackout check BEFORE this module
  sees them (``blackout.py``); the fetcher re-checks through ``before_send`` so a direct call
  cannot bypass it.
"""

from __future__ import annotations

import datetime
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from ..fetch import FetchError, FetchRefused
from .errors import JrdbCredentialsMissing, JrdbNotFound

MEMBER_BASE = "https://jrdb.com/member/"
USER_ENV = "JRDB_MEMBER_USER"
PASSWORD_ENV = "JRDB_MEMBER_PASSWORD"  # noqa: S105 — the NAME of the variable, not a value
INTERVAL_ENV = "HORSERACING_JRDB_MIN_INTERVAL_S"
DEFAULT_INTERVAL_S = 5.0
MIN_INTERVAL_FLOOR_S = 2.0
TIMEOUT_S = 60.0
USER_AGENT = "horseracing-jrdb/178 (+member; personal use)"

#: file kind → directory name on the member site (first letter upper-case; Ks/Cs for the masters)
KIND_DIRS: dict[str, str] = {
    "BAC": "Bac",
    "KYI": "Kyi",
    "KYH": "Kyh",
    "KYG": "Kyg",
    "UKC": "Ukc",
    "HJC": "Hjc",
    "SED": "Sed",
    "SEC": "Sec",
    "SRB": "Sed",  # shipped inside the SED archive
    "TYB": "Tyb",
    "SKB": "Skb",
    "KAB": "Kab",
    "CYB": "Cyb",
    "CHA": "Cha",
    "KZA": "Ks",
    "KSA": "Ks",
    "CZA": "Cs",
    "CSA": "Cs",
    "OZ": "Oz",
    "OW": "Ow",
    "OU": "Ou",
    "OT": "Ot",
    "OV": "Ov",
    "MZA": "Ms",
    "MSA": "Ms",
}


def _dir(kind: str) -> str:
    k = kind.upper()
    try:
        return KIND_DIRS[k]
    except KeyError:
        raise ValueError(f"unknown JRDB kind {kind!r}") from None


def _yymmdd(day: datetime.date) -> str:
    return day.strftime("%y%m%d")


def url_yearly(kind: str, year: int) -> str:
    """年度パック: ``datazip/<Kind>/<KIND>_<YYYY>.zip`` (1999〜, latest format only)."""

    return f"{MEMBER_BASE}datazip/{_dir(kind)}/{kind.upper()}_{year:04d}.zip"


def url_single(kind: str, day: datetime.date, fmt: str = "zip") -> str:
    """単体: zip ``datazip/<Kind>/<YYYY>/<KIND>yymmdd.zip``
    or lzh ``data/<Kind>/<KIND>yymmdd.lzh``."""

    k = kind.upper()
    if fmt == "zip":
        return f"{MEMBER_BASE}datazip/{_dir(k)}/{day.year:04d}/{k}{_yymmdd(day)}.zip"
    if fmt == "lzh":
        return f"{MEMBER_BASE}data/{_dir(k)}/{k}{_yymmdd(day)}.lzh"
    raise ValueError(f"unknown archive format {fmt!r}")


def url_person_master(kind: str, day: datetime.date) -> str:
    """騎手/調教師マスタ: ``datazip/Ks|Cs/<YYYY>/KZA<yymmdd>.zip`` (no index page)."""

    k = kind.upper()
    if k not in ("KZA", "KSA", "CZA", "CSA"):
        raise ValueError("person master kinds are KZA/KSA/CZA/CSA")
    return url_single(k, day, "zip")


def url_tyokuzen(day: datetime.date) -> str:
    """直前情報 随時更新版: ``<YYYYMMDD>/tyokuzen/TYB<yymmdd>.zip`` (T−14 分前後で更新)."""

    return f"{MEMBER_BASE}{day.strftime('%Y%m%d')}/tyokuzen/TYB{_yymmdd(day)}.zip"


def source_url_sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def redact(url: str) -> str:
    """What logs and exceptions may say about a member URL: its basename and a short sha."""

    base = url.rsplit("/", 1)[-1] or "<root>"
    return f"member:{source_url_sha256(url)[:12]}:{base}"


@dataclass(frozen=True)
class Credentials:
    user: str
    password: str

    def __repr__(self) -> str:  # never echo: the member id is a credential too
        return "Credentials(user=***, password=***)"


def credentials_from_env(env: dict[str, str] | None = None) -> Credentials:
    import os

    e = os.environ if env is None else env
    user = (e.get(USER_ENV) or "").strip()
    password = e.get(PASSWORD_ENV) or ""
    if not user or not password:
        raise JrdbCredentialsMissing(
            f"{USER_ENV} / {PASSWORD_ENV} must both be set (values are never logged)"
        )
    return Credentials(user, password)


def interval_from_env(
    env: dict[str, str] | None = None, *, default: float = DEFAULT_INTERVAL_S
) -> float:
    import os

    e = os.environ if env is None else env
    raw = (e.get(INTERVAL_ENV) or "").strip()
    if not raw:
        value = float(default)
    else:
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{INTERVAL_ENV} is not a number") from None
    if value < MIN_INTERVAL_FLOOR_S:
        raise ValueError(
            f"{INTERVAL_ENV}={value:g} is below the floor of {MIN_INTERVAL_FLOOR_S:g}s"
        )
    return value


@dataclass(frozen=True)
class HeadInfo:
    status: int
    last_modified: datetime.datetime | None
    content_length: int | None

    def same_as(self, other: HeadInfo | None) -> bool:
        if other is None:
            return False
        return (
            self.last_modified is not None
            and self.last_modified == other.last_modified
            and self.content_length == other.content_length
        )


@dataclass(frozen=True)
class FetchedFile:
    url_sha256: str
    name: str  # basename of the URL (``KYI_2008.zip``)
    raw: bytes
    fetched_at: datetime.datetime
    last_modified: datetime.datetime | None
    content_length: int | None


def _parse_last_modified(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


def _parse_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class JrdbFetcher:
    """Basic-auth client for the member site. See the module docstring for the contract."""

    def __init__(
        self,
        *,
        credentials: Credentials,
        min_interval_s: float = DEFAULT_INTERVAL_S,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        before_send: Callable[[str], None] | None = None,
    ) -> None:
        if min_interval_s < MIN_INTERVAL_FLOOR_S:
            raise ValueError(f"min_interval_s below the floor of {MIN_INTERVAL_FLOOR_S:g}s")
        self._auth = httpx.BasicAuth(credentials.user, credentials.password)
        self.min_interval_s = float(min_interval_s)
        self._client = client or httpx.Client(
            follow_redirects=False, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}
        )
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._before_send = before_send
        self._last_send: float | None = None
        self.requests_sent = 0

    # --- politeness -------------------------------------------------------------------------
    def _wait_turn(self) -> None:
        if self._last_send is not None:
            due = self._last_send + self.min_interval_s
            now = self._monotonic()
            if now < due:
                self._sleep(due - now)

    def _send(self, method: str, url: str) -> httpx.Response:
        if not url.startswith(MEMBER_BASE):
            raise ValueError("JrdbFetcher only talks to the member site")
        if self._before_send is not None:
            self._before_send(url)
        self._wait_turn()
        try:
            resp = self._client.request(method, url, auth=self._auth)
        except httpx.HTTPError as exc:
            raise FetchError(f"{method} {redact(url)}: {type(exc).__name__}") from None
        finally:
            self._last_send = self._monotonic()
            self.requests_sent += 1
        return resp

    def _classify(self, resp: httpx.Response, url: str) -> None:
        if resp.status_code in (401, 403):
            raise FetchRefused(resp.status_code, redact(url))
        if resp.status_code in (429, 503):
            retry = resp.headers.get("Retry-After")
            retry_s: float | None = None
            if retry and retry.strip().isdigit():
                retry_s = float(retry.strip())
            raise FetchRefused(resp.status_code, redact(url), retry_after_s=retry_s)
        if resp.status_code == 404:
            raise JrdbNotFound(f"not published: {redact(url)}")
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code} for {redact(url)}")

    # --- public -----------------------------------------------------------------------------
    def head(self, url: str) -> HeadInfo:
        resp = self._send("HEAD", url)
        self._classify(resp, url)
        return HeadInfo(
            resp.status_code,
            _parse_last_modified(resp.headers.get("Last-Modified")),
            _parse_length(resp.headers.get("Content-Length")),
        )

    def get(self, url: str, *, known: HeadInfo | None = None) -> FetchedFile | None:
        """Download one file. With ``known`` (the previous version's HEAD), a HEAD is sent first
        and ``None`` is returned when nothing changed (no GET)."""

        if known is not None:
            current = self.head(url)
            if current.same_as(known):
                return None
        resp = self._send("GET", url)
        self._classify(resp, url)
        return FetchedFile(
            url_sha256=source_url_sha256(url),
            name=url.rsplit("/", 1)[-1],
            raw=resp.content,
            fetched_at=self._clock(),
            last_modified=_parse_last_modified(resp.headers.get("Last-Modified")),
            content_length=_parse_length(resp.headers.get("Content-Length")),
        )


__all__ = [
    "DEFAULT_INTERVAL_S",
    "INTERVAL_ENV",
    "KIND_DIRS",
    "MIN_INTERVAL_FLOOR_S",
    "PASSWORD_ENV",
    "USER_ENV",
    "Credentials",
    "FetchedFile",
    "HeadInfo",
    "JrdbFetcher",
    "credentials_from_env",
    "interval_from_env",
    "redact",
    "source_url_sha256",
    "url_person_master",
    "url_single",
    "url_tyokuzen",
    "url_yearly",
]
