"""Durable, TTL'd robots.txt cache shared by every fetcher and every process.

`HttpFetcher._robots` is an INSTANCE dict, and both the ops worker (a fetcher per loop iteration)
and capture (a fetcher per capture) build fetchers constantly — so robots.txt was re-fetched
almost every time. Measured against the operator's 1-request-per-MINUTE budget that is not a
rounding error: with ~55 race refreshes and ~32 capture-bearing predicts a day across two
netkeiba hosts, robots alone can account for a third to a half of the entire daily request
budget. It also made interactive capture structurally impossible — robots + the odds API is two
requests, and two requests cannot fit inside a 10-second deadline at 60-second spacing.

## What may and may not be cached

Only OUTCOMES THAT ARE ABOUT THE SITE get stored. Outcomes that are about the moment — a 5xx, a
dropped connection, a refusal — are never written, because persisting them would freeze a
transient failure into policy for a whole TTL. This matters in one direction especially: the
caller currently treats an unreachable robots as "allow", and caching that would keep fetching
paths robots forbids for 24 hours.

| robots response      | stored?                | decision for THIS request      |
|----------------------|------------------------|--------------------------------|
| 200                  | yes — raw body         | parse and enforce              |
| 404 / 410            | yes — explicit absence | allow (the site has no robots) |
| 5xx / network error  | **no**                 | caller's existing behaviour    |
| 429 / 403 / 400      | **no**                 | refusal propagates             |

RFC 9309 §2.3.1 additionally says an unreachable robots should mean *complete disallow*, which is
stricter than this repo's current "allow". That is a scraping-behaviour change rather than a
caching one, so it is deliberately NOT made here — see the 093 spec.

## Failure isolation

A cache read failure is a MISS. A cache write failure is ignored. Neither may ever change the
authorization decision: the dangerous shape is "robots said Disallow, the write failed, and the
error handler turned that into allow".
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import errno
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

#: Bump when the on-disk shape changes; older entries are then ignored rather than misread.
SCHEMA_VERSION = 1
#: RFC 9309 §2.4 recommends caching robots for at most 24 hours.
DEFAULT_TTL_S = 24 * 60 * 60
#: A robots.txt larger than this is not something we want to hold in memory per fetcher.
MAX_BODY_BYTES = 512 * 1024
#: Never block a capture's 10-second deadline waiting for another process's robots fetch.
LOCK_TIMEOUT_S = 5.0

RULES = "rules"
ABSENT = "absent"

_process_locks: dict[str, threading.Lock] = {}
_process_locks_guard = threading.Lock()

#: Every production entry point must resolve to the SAME ABSOLUTE directory, or the cache silently
#: does nothing where it matters most. The capture subprocess runs with cwd=live/, so a relative
#: default would resolve differently there and give capture its own empty cache — exactly the
#: caller whose 10-second deadline the cache exists to make satisfiable.


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def origin_of(url: str) -> str:
    """Scheme + netloc, including a non-default port — the exact scope robots applies to."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class RobotsEntry:
    """One cached robots outcome. ``body`` is empty for ``ABSENT``."""

    origin: str
    outcome: str          #: RULES | ABSENT
    status: int
    fetched_at: datetime.datetime
    body: bytes

    def is_fresh(self, *, now: datetime.datetime, ttl_s: float) -> bool:
        age = (now - self.fetched_at).total_seconds()
        # A negative age means a future timestamp (clock skew, or a hand-edited file). Treat it as
        # stale rather than as indefinitely fresh — the failure mode of "fresh forever" is that we
        # never notice robots changing.
        return 0 <= age < ttl_s


class RobotsCache:
    """File-backed store of robots outcomes, safe across threads and processes."""

    def __init__(
        self,
        directory: str | Path,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        clock=_utcnow,
    ) -> None:
        self.directory = Path(directory)
        self.ttl_s = float(ttl_s)
        self._clock = clock

    # --- paths ---------------------------------------------------------------
    def _key(self, origin: str) -> str:
        return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:20]

    def _entry_path(self, origin: str) -> Path:
        return self.directory / f"{self._key(origin)}.json"

    def _lock_path(self, origin: str) -> Path:
        # A SEPARATE lock file: locking the data file itself is unsafe because os.replace swaps
        # the inode out from under any holder.
        return self.directory / f"{self._key(origin)}.lock"

    # --- read ----------------------------------------------------------------
    def get(self, origin: str) -> RobotsEntry | None:
        """Fresh entry for ``origin``, or None (missing, stale, corrupt, or foreign)."""
        try:
            raw = self._entry_path(origin).read_text(encoding="utf-8")
            doc = json.loads(raw)
            if doc.get("schema_version") != SCHEMA_VERSION:
                return None
            if doc.get("origin") != origin:  # hash collision or a copied file
                return None
            outcome = doc.get("outcome")
            if outcome not in (RULES, ABSENT):
                return None
            body = base64.b64decode(doc.get("body") or "")
            if len(body) > MAX_BODY_BYTES:
                return None
            entry = RobotsEntry(
                origin=origin,
                outcome=outcome,
                status=int(doc["status"]),
                fetched_at=datetime.datetime.fromisoformat(doc["fetched_at"]),
                body=body,
            )
        except Exception:  # noqa: BLE001 — any unreadable entry is simply a miss
            return None
        if entry.fetched_at.tzinfo is None:
            return None
        return entry if entry.is_fresh(now=self._clock(), ttl_s=self.ttl_s) else None

    # --- write ---------------------------------------------------------------
    def put(self, origin: str, *, outcome: str, status: int, body: bytes = b"") -> None:
        """Store an outcome. Best-effort: a failure here must never change a robots decision."""
        if outcome not in (RULES, ABSENT):
            raise ValueError(f"refusing to cache outcome {outcome!r}")
        if len(body) > MAX_BODY_BYTES:
            return
        doc = {
            "schema_version": SCHEMA_VERSION,
            "origin": origin,
            "outcome": outcome,
            "status": int(status),
            "fetched_at": self._clock().isoformat(),
            "body": base64.b64encode(body).decode("ascii"),
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._entry_path(origin)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            tmp.replace(path)  # atomic: a reader sees the old entry or the new one, never a mix
        except Exception:  # noqa: BLE001 — best effort
            with contextlib.suppress(Exception):
                tmp.unlink()

    # --- single-flight -------------------------------------------------------
    @contextlib.contextmanager
    def refresh_lock(self, origin: str):
        """Serialize the robots fetch for one origin across threads AND processes.

        Without it, two IO threads plus a CLI plus a capture subprocess all reaching the TTL
        boundary together each spend a request slot on the same robots.txt — at 1 request per
        minute that is four minutes of budget for one file. Bounded so a capture with a ten-second
        deadline can never hang here.
        """
        with _process_locks_guard:
            lock = _process_locks.setdefault(origin, threading.Lock())
        acquired = lock.acquire(timeout=LOCK_TIMEOUT_S)
        fd = None
        try:
            if acquired:
                fd = self._acquire_file_lock(origin)
            yield acquired
        finally:
            if fd is not None:
                with contextlib.suppress(Exception):
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
            if acquired:
                lock.release()

    def _acquire_file_lock(self, origin: str) -> int | None:
        try:
            import fcntl
        except ImportError:  # pragma: no cover — POSIX only; degrade to the thread lock
            return None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._lock_path(origin), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            return None
        deadline = _utcnow() + datetime.timedelta(seconds=LOCK_TIMEOUT_S)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN) or _utcnow() >= deadline:
                    os.close(fd)
                    return None
                threading.Event().wait(0.05)

ROBOTS_CACHE_ENV = "HORSERACING_ROBOTS_CACHE_DIR"
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "robots_cache"


def shared_cache(*, ttl_s: float = DEFAULT_TTL_S) -> RobotsCache | None:
    """The one cache every production fetcher should use. Env-overridable; empty string disables."""
    raw = os.environ.get(ROBOTS_CACHE_ENV)
    directory = _DEFAULT_DIR if raw is None else Path(raw.strip()) if raw.strip() else None
    return None if directory is None else RobotsCache(directory.resolve(), ttl_s=ttl_s)
