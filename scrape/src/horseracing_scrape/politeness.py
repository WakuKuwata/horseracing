"""Cross-process request politeness for netkeiba: reserve a slot, then send.

Moved here from ``horseracing_live.chaos_politeness`` (feature 086) so that ops and the scrape
CLI can use it too. ``ops`` MUST NOT import ``horseracing_live`` (machine-enforced at
ops/tests/integration/test_boundary.py), and this package already owns ``HttpFetcher`` and
depends on the DB, so it is the only place all three callers can share.

``horseracing_live.chaos_politeness`` re-exports the SAME class objects, so ``except
PolitenessRefused`` written against either module still catches the same exception.

The only thing this needed from live was "how much of my deadline is left?", which is now the
one-method ``DeadlineSource`` protocol rather than an import of a live-private class.
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from horseracing_db.models import FetchThrottleState
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from .fetch import FetchRefused, _domain

PolitenessReason = Literal["source_cooldown", "throttle_backlog", "deadline_exceeded"]

#: One request per this many seconds, per throttle key. The DEFAULT is deliberately the polite
#: production value rather than 1.0: an interval is a property of the SOURCE, and a caller that
#: quietly picks a weaker one shortens the contract for everybody sharing the row.
_DEFAULT_MIN_INTERVAL_S = 1.0
_DEFAULT_MAX_WAIT_S = 3.0
_MAX_RETRY_AFTER_S = 6 * 60 * 60
#: Statuses that install a shared cooldown. netkeiba's real block is a bare 400 with an empty
#: body (see REFUSAL_STATUSES in fetch.py); 403/429 are the documented ones.
_COOLDOWN_S = {429: 30 * 60, 403: 60 * 60}
#: A reservation is timestamped immediately before the socket send. This conservative margin
#: keeps server-observed arrivals at least the contractual interval apart despite commit overhead.
_SEND_SAFETY_MARGIN_S = 0.05


@runtime_checkable
class DeadlineSource(Protocol):
    """Anything that can say how much wall-clock budget the current fetch has left."""

    def remaining(self) -> float | None: ...


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




class RequestPoliteness:
    """Reserve request slots and persist source cooldowns in independent DB sessions."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        max_wait_s: float = _DEFAULT_MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        deadline_budget: DeadlineSource | None = None,
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
        deadline_budget: DeadlineSource | None = None,
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
