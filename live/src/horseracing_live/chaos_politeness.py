"""Cross-process request reservations for Feature 086 chaos capture.

The capture transaction deliberately stays open across the external fetch so it can hold the
per-race advisory lock.  Every operation in this module therefore uses its own short-lived
session: reservations and cooldowns must commit independently of capture success or rollback.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Literal, Protocol

import httpx
from horseracing_db.session import create_db_engine
from horseracing_scrape import robots_cache
from horseracing_scrape.fetch import FetchRefused, HttpFetcher
from horseracing_scrape.politeness import (  # moved to scrape so ops can use it too;
    PolitenessReason,  # these are the SAME objects, not wrappers, so `except
    PolitenessRefused,  # PolitenessRefused` written against either module still works
    RequestPoliteness,
    ReservationDecision,
)
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

CaptureTrigger = Literal[
    "predict_manual",
    "predict_auto",
    "daily_operational",
    "explicit_command",
]

_CAPTURE_USER_AGENT = "horseracing-live/0.1 (personal use; contact via repo)"
_DEFAULT_MIN_INTERVAL_S = 1.0
_DEFAULT_MAX_WAIT_S = 3.0
_MAX_RETRY_AFTER_S = 6 * 60 * 60
_COOLDOWN_S = {429: 30 * 60, 403: 60 * 60}
# A reservation is timestamped immediately before the socket send.  This conservative margin
# keeps server-observed arrivals at least the contractual interval apart despite commit overhead.
_SEND_SAFETY_MARGIN_S = 0.05


def deadline_for(trigger: str) -> float:
    """Return the canonical per-race wall-clock budget for a capture trigger."""

    deadlines: dict[str, float] = {
        "predict_manual": 10.0,
        "predict_auto": 10.0,
        "daily_operational": 30.0,
        "explicit_command": 30.0,
    }
    try:
        return deadlines[trigger]
    except KeyError as exc:
        raise ValueError(f"unsupported capture trigger: {trigger!r}") from exc


class _DeadlineBudget:
    """Thread-local monotonic deadline established separately for each top-level fetch."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._local = threading.local()

    @contextmanager
    def limit(self, seconds: float | None):
        previous = getattr(self._local, "deadline", None)
        self._local.deadline = None if seconds is None else self._clock() + seconds
        try:
            yield
        finally:
            self._local.deadline = previous

    @contextmanager
    def limit_until(self, deadline: float | None):
        """Use a caller-owned absolute deadline so fetch, parse, and save share one clock."""

        previous = getattr(self._local, "deadline", None)
        self._local.deadline = deadline
        try:
            yield
        finally:
            self._local.deadline = previous

    def remaining(self) -> float | None:
        deadline = getattr(self._local, "deadline", None)
        if deadline is None:
            return None
        return max(0.0, deadline - self._clock())


class _PolitenessPolicy(Protocol):
    def pre_request(self, url: str) -> None: ...

    def record_refusal(
        self,
        refusal: FetchRefused,
        *,
        request_url: str | None = None,
    ) -> None: ...


class _DeadlineHttpClient:
    """Synchronous bridge to a cancellable async request with the remaining total budget."""

    def __init__(
        self,
        *,
        headers: dict[str, str],
        timeout: httpx.Timeout,
        deadline_budget: _DeadlineBudget,
    ) -> None:
        self.headers = dict(headers)
        self.timeout = timeout
        self._deadline_budget = deadline_budget

    def get(self, url: str):
        async def request():
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                remaining = self._deadline_budget.remaining()
                if remaining is None:
                    return await client.get(url)
                try:
                    async with asyncio.timeout(remaining):
                        return await client.get(url)
                except TimeoutError as exc:
                    raise PolitenessRefused("deadline_exceeded", url) from exc

        return asyncio.run(request())

    def close(self) -> None:
        pass


class CaptureHttpFetcher(HttpFetcher):
    """Capture-only HttpFetcher that records real source refusals before re-raising."""

    def __init__(
        self,
        *,
        policy: _PolitenessPolicy,
        deadline_budget: _DeadlineBudget,
        deadline_s: float | None,
        deadline: float | None = None,
        owned_engine: Engine | None = None,
        **kwargs,
    ) -> None:
        super().__init__(pre_request=policy.pre_request, **kwargs)
        self.politeness = policy
        self._deadline_budget = deadline_budget
        self._deadline_s = deadline_s
        self._deadline = deadline
        self._owned_engine = owned_engine

    @property
    def client(self):
        return self._client

    def _rate_limit(self, _domain: str) -> None:
        """Let the DB reservation be the only capture-path rate limiter.

        ``HttpFetcher`` normally sleeps here before its injectable
        ``pre_request`` seam.  That duplicate, process-local wait cannot inspect
        the capture-wide deadline; the DB-backed policy already reserves both
        robots and main requests and performs its wait inside that deadline.
        """

    def get(self, url: str, *, use_cache: bool = True) -> str:
        limit = (
            self._deadline_budget.limit_until(self._deadline)
            if self._deadline is not None
            else self._deadline_budget.limit(self._deadline_s)
        )
        with limit:
            try:
                return super().get(url, use_cache=use_cache)
            except PolitenessRefused:
                raise
            except FetchRefused as refusal:
                self.politeness.record_refusal(refusal, request_url=url)
                raise

    def set_deadline(self, deadline: float) -> None:
        """Bind the next capture to its caller-owned absolute monotonic deadline."""

        self._deadline = float(deadline)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            close()
        if self._owned_engine is not None:
            self._owned_engine.dispose()


def make_capture_fetcher(
    *,
    engine: Engine | None = None,
    database_url: str | None = None,
    session_factory: Callable[[], Session] | None = None,
    policy: _PolitenessPolicy | None = None,
    client=None,
    min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
    max_wait_s: float = _DEFAULT_MAX_WAIT_S,
    deadline_s: float | None = None,
    deadline: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    respect_robots: bool = True,
) -> CaptureHttpFetcher:
    """Build the capture-only client without changing scrape's shared ingest factory."""

    if deadline_s is not None and deadline_s <= 0:
        raise ValueError("deadline_s must be positive")
    if deadline_s is not None and deadline is not None:
        raise ValueError("deadline_s and deadline are mutually exclusive")
    if policy is not None and any(
        value is not None for value in (engine, database_url, session_factory)
    ):
        raise ValueError("policy cannot be combined with DB construction arguments")
    if engine is not None and database_url is not None:
        raise ValueError("engine and database_url are mutually exclusive")

    deadline_budget = _DeadlineBudget(clock)
    owned_engine: Engine | None = None
    if policy is None:
        if session_factory is None:
            active_engine = engine
            if active_engine is None:
                owned_engine = create_db_engine(database_url)
                active_engine = owned_engine
            session_factory = sessionmaker(bind=active_engine, expire_on_commit=False)
        policy = RequestPoliteness(
            session_factory,
            min_interval_s=min_interval_s,
            max_wait_s=max_wait_s,
            sleep=sleep,
            deadline_budget=deadline_budget,
        )

    timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
    if client is None:
        # Always use the deadline-aware bridge. A CLI-created fetcher receives
        # its absolute per-race deadline later via set_deadline(); choosing the
        # plain synchronous client here would leave an in-flight request alive
        # past that shared deadline.
        client = _DeadlineHttpClient(
            headers={"User-Agent": _CAPTURE_USER_AGENT},
            timeout=timeout,
            deadline_budget=deadline_budget,
        )

    return CaptureHttpFetcher(
        policy=policy,
        deadline_budget=deadline_budget,
        deadline_s=deadline_s,
        deadline=deadline,
        owned_engine=owned_engine,
        user_agent=_CAPTURE_USER_AGENT,
        min_interval_s=min_interval_s,
        cache_dir=None,
        # Capture is the caller the robots cache exists for: robots+odds is two requests and
        # cannot fit a 10-second deadline at 60-second spacing. A fresh entry makes it one.
        robots_cache_store=robots_cache.shared_cache(),
        max_retries=1,
        client=client,
        sleep=sleep,
        clock=clock,
        respect_robots=respect_robots,
    )


__all__ = [
    "CaptureHttpFetcher",
    "CaptureTrigger",
    "PolitenessReason",
    "PolitenessRefused",
    "RequestPoliteness",
    "ReservationDecision",
    "deadline_for",
    "make_capture_fetcher",
]
