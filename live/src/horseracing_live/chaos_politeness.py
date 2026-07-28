"""Cross-process request reservations for Feature 086 chaos capture.

The capture transaction deliberately stays open across the external fetch so it can hold the
per-race advisory lock.  Every operation in this module therefore uses its own short-lived
session: reservations and cooldowns must commit independently of capture success or rollback.
"""

from __future__ import annotations

import asyncio
import datetime
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from horseracing_db.models import FetchThrottleState
from horseracing_db.session import create_db_engine
from horseracing_scrape.fetch import FetchRefused, HttpFetcher, _domain
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

CaptureTrigger = Literal[
    "predict_manual",
    "predict_auto",
    "daily_operational",
    "explicit_command",
]
PolitenessReason = Literal[
    "source_cooldown",
    "throttle_backlog",
    "deadline_exceeded",
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


@dataclass(frozen=True)
class ReservationDecision:
    """Result of reserving one outbound HTTP request."""

    domain: str
    allowed: bool
    reason: PolitenessReason | None = None
    wait_s: float = 0.0


class PolitenessRefused(FetchRefused):
    """A local reservation/deadline refusal carrying the capture skip reason."""

    def __init__(self, reason: PolitenessReason, url: str):
        self.reason = reason
        status_code = 408 if reason == "deadline_exceeded" else 429
        super().__init__(status_code, url)
        self.args = (f"request skipped ({reason}) for {url}",)


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


class RequestPoliteness:
    """Reserve request slots and persist source cooldowns in independent DB sessions."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        max_wait_s: float = _DEFAULT_MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        deadline_budget: _DeadlineBudget | None = None,
    ) -> None:
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be non-negative")
        if max_wait_s < 0:
            raise ValueError("max_wait_s must be non-negative")
        self._session_factory = session_factory
        self.min_interval_s = float(min_interval_s)
        self.max_wait_s = float(max_wait_s)
        self._sleep = sleep
        self._deadline_budget = deadline_budget

    @classmethod
    def for_engine(
        cls,
        engine: Engine,
        *,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        max_wait_s: float = _DEFAULT_MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        deadline_budget: _DeadlineBudget | None = None,
    ) -> RequestPoliteness:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        return cls(
            factory,
            min_interval_s=min_interval_s,
            max_wait_s=max_wait_s,
            sleep=sleep,
            deadline_budget=deadline_budget,
        )

    def reserve(self, url: str) -> ReservationDecision:
        """Atomically reserve one request without sleeping or holding the row lock."""

        domain = _domain(url)
        with self._session_factory() as session, session.begin():
            session.execute(
                insert(FetchThrottleState)
                .values(
                    domain=domain,
                    next_allowed_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing(index_elements=[FetchThrottleState.domain])
            )
            row = session.scalar(
                select(FetchThrottleState)
                .where(FetchThrottleState.domain == domain)
                .with_for_update()
            )
            if row is None:  # pragma: no cover - INSERT + PK make this structurally unreachable
                raise RuntimeError(f"failed to initialize fetch throttle row for {domain}")
            now = session.scalar(select(func.clock_timestamp()))
            if now is None:  # pragma: no cover - PostgreSQL always returns clock_timestamp()
                raise RuntimeError("database did not return clock_timestamp()")

            if row.blocked_until is not None and row.blocked_until > now:
                return ReservationDecision(
                    domain=domain,
                    allowed=False,
                    reason="source_cooldown",
                )

            wait_s = max(0.0, (row.next_allowed_at - now).total_seconds())
            if wait_s > self.max_wait_s:
                return ReservationDecision(
                    domain=domain,
                    allowed=False,
                    reason="throttle_backlog",
                    wait_s=wait_s,
                )

            row.next_allowed_at = now + datetime.timedelta(
                seconds=wait_s + self.min_interval_s
            )
            row.updated_at = now
            return ReservationDecision(domain=domain, allowed=True, wait_s=wait_s)

    def pre_request(self, url: str) -> None:
        """Reserve, wait locally, then re-read cooldown immediately before the send."""

        self._ensure_deadline(url)
        decision = self.reserve(url)
        if not decision.allowed:
            assert decision.reason is not None
            raise PolitenessRefused(decision.reason, url)

        if decision.wait_s > 0:
            remaining = self._remaining()
            required_wait = decision.wait_s + _SEND_SAFETY_MARGIN_S
            if remaining is not None and required_wait >= remaining:
                raise PolitenessRefused("deadline_exceeded", url)
            self._sleep(required_wait)

        self._ensure_deadline(url)
        if self._is_blocked(decision.domain):
            raise PolitenessRefused("source_cooldown", url)
        self._ensure_deadline(url)

    def record_refusal(
        self,
        refusal: FetchRefused,
        *,
        request_url: str | None = None,
    ) -> None:
        """Commit a real HTTP 403/429 cooldown independently of the capture transaction."""

        if isinstance(refusal, PolitenessRefused):
            return
        if refusal.status_code not in _COOLDOWN_S:
            return
        refused_url = refusal.url or request_url
        if refused_url is None:
            raise ValueError("FetchRefused.url is required to persist a cooldown")

        retry_after_s = refusal.retry_after_s
        cooldown_s = (
            float(_COOLDOWN_S[refusal.status_code])
            if retry_after_s is None
            else min(max(float(retry_after_s), 0.0), float(_MAX_RETRY_AFTER_S))
        )
        domain = _domain(refused_url)
        reason = f"http_{refusal.status_code}"

        with self._session_factory() as session, session.begin():
            session.execute(
                insert(FetchThrottleState)
                .values(
                    domain=domain,
                    next_allowed_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
                .on_conflict_do_nothing(index_elements=[FetchThrottleState.domain])
            )
            row = session.scalar(
                select(FetchThrottleState)
                .where(FetchThrottleState.domain == domain)
                .with_for_update()
            )
            if row is None:  # pragma: no cover - INSERT + PK make this structurally unreachable
                raise RuntimeError(f"failed to initialize fetch throttle row for {domain}")
            now = session.scalar(select(func.clock_timestamp()))
            if now is None:  # pragma: no cover
                raise RuntimeError("database did not return clock_timestamp()")
            proposed_until = now + datetime.timedelta(seconds=cooldown_s)
            if row.blocked_until is None or proposed_until > row.blocked_until:
                row.blocked_until = proposed_until
                row.block_reason = reason
            row.updated_at = now

    def _remaining(self) -> float | None:
        return None if self._deadline_budget is None else self._deadline_budget.remaining()

    def _ensure_deadline(self, url: str) -> None:
        remaining = self._remaining()
        if remaining is not None and remaining <= 0:
            raise PolitenessRefused("deadline_exceeded", url)

    def _is_blocked(self, domain: str) -> bool:
        with self._session_factory() as session:
            now = session.scalar(select(func.clock_timestamp()))
            blocked_until = session.scalar(
                select(FetchThrottleState.blocked_until).where(
                    FetchThrottleState.domain == domain
                )
            )
        return bool(
            now is not None
            and blocked_until is not None
            and blocked_until > now
        )


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
