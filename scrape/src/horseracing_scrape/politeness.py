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
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from horseracing_db.models import FetchThrottleState
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from .fetch import FetchRefused, _domain

PolitenessReason = Literal["source_cooldown", "throttle_backlog", "deadline_exceeded"]

#: Hosts that share one operator, and therefore one request budget. `race.netkeiba.com` and
#: `db.netkeiba.com` are the same site; keying the throttle by hostname gave each its own bucket
#: and silently doubled the rate.
_NETKEIBA_KEY = "netkeiba.com"
#: The source's contract, in seconds between requests. Env-overridable so the operator can change
#: the policy in ONE place; a caller cannot weaken it (see RequestPoliteness._interval_for).
SOURCE_INTERVAL_ENV = "HORSERACING_NETKEIBA_MIN_INTERVAL_S"
_DEFAULT_NETKEIBA_INTERVAL_S = 1.0


def throttle_key(url: str) -> str:
    """The bucket a request is throttled against.

    Deliberately NOT ``_domain`` — that one builds the robots URL (``{domain}/robots.txt``), so
    normalising it would fetch robots from the wrong host. This is only ever a dictionary key.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host == _NETKEIBA_KEY or host.endswith("." + _NETKEIBA_KEY):
        return _NETKEIBA_KEY
    return _domain(url)


def source_interval_s(key: str) -> float:
    """Seconds between requests owed to ``key``. One contract, read from one place."""
    if key != _NETKEIBA_KEY:
        return 0.0
    raw = os.environ.get(SOURCE_INTERVAL_ENV)
    if not raw:
        return _DEFAULT_NETKEIBA_INTERVAL_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_NETKEIBA_INTERVAL_S

#: One request per this many seconds, per throttle key. The DEFAULT is deliberately the polite
#: production value rather than 1.0: an interval is a property of the SOURCE, and a caller that
#: quietly picks a weaker one shortens the contract for everybody sharing the row.
_DEFAULT_MIN_INTERVAL_S = 1.0
_DEFAULT_MAX_WAIT_S = 3.0
_MAX_RETRY_AFTER_S = 6 * 60 * 60
#: Statuses that install a shared cooldown. netkeiba's real block is a bare 400 with an empty
#: body (see REFUSAL_STATUSES in fetch.py); 403/429 are the documented ones.
_COOLDOWN_S = {400: 30 * 60, 429: 30 * 60, 403: 60 * 60}
#: A reservation is timestamped immediately before the socket send. This conservative margin
#: keeps server-observed arrivals at least the contractual interval apart despite commit overhead.
_SEND_SAFETY_MARGIN_S = 0.05
#: How often an unbounded caller rechecks a source cooldown it is waiting out.
_COOLDOWN_POLL_S = 30.0


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
        max_wait_s: float | None = _DEFAULT_MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        deadline_budget: DeadlineSource | None = None,
    ) -> None:
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be non-negative")
        if max_wait_s is not None and max_wait_s < 0:
            raise ValueError("max_wait_s must be non-negative")
        self._session_factory = session_factory
        self.min_interval_s = float(min_interval_s)
        #: None = background contract: wait for the slot (and through a cooldown) instead of
        #: refusing. A number = bounded caller (capture): refuse once the wait exceeds it.
        self.max_wait_s = None if max_wait_s is None else float(max_wait_s)
        self._sleep = sleep
        self._deadline_budget = deadline_budget

    @classmethod
    def for_engine(
        cls,
        engine: Engine,
        *,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        max_wait_s: float | None = _DEFAULT_MAX_WAIT_S,
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
        """Try to claim the send slot FOR NOW. Never books a slot in the future.

        The previous shape advanced ``next_allowed_at`` and then slept locally, which is a booking
        for a future instant. That is unsafe on a machine that suspends: two waiters holding
        T+60 and T+120 both wake late (laptop resume), the first sends at T+110 and the second at
        T+120 — ten seconds apart under a sixty-second contract. Here a caller only ever takes the
        slot when it is ALREADY due, so a late wake just finds the slot taken and queues again.

        Returns ``allowed=False`` with ``wait_s`` when the slot is not yet due; the caller decides
        whether to wait (background) or give up (deadline-bound capture).
        """

        domain = throttle_key(url)
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
            if wait_s > 0:
                # Not due yet. Do NOT take the slot — just report how long it is.
                return ReservationDecision(
                    domain=domain,
                    allowed=False,
                    reason="throttle_backlog",
                    wait_s=wait_s,
                )

            # Due now: take it, and push the next slot a full interval from THIS instant.
            row.next_allowed_at = now + datetime.timedelta(seconds=self._interval_for(domain))
            row.updated_at = now
            return ReservationDecision(domain=domain, allowed=True, wait_s=0.0)

    def _interval_for(self, key: str) -> float:
        """The interval that actually applies. A caller may tighten it, never loosen it.

        An interval belongs to the SOURCE, not to whoever happens to be calling. Capture used to
        construct its policy with the 1.0 default while ops asked for 60 — and because both write
        the same row, capture's reservation advanced it by one second and quietly shortened the
        contract for everybody.
        """
        return max(self.min_interval_s, source_interval_s(key))

    def pre_request(self, url: str) -> None:
        """Block until this request may be sent, or refuse.

        Two callers with opposite correct answers share this code:

        * **capture** has a hard deadline (a race is about to start), so waiting past it is
          pointless — refuse and record the skip.
        * **the daily job** has no deadline and every dropped request is lost ingest. Under the
          old fixed ``max_wait_s=3`` it refused as soon as the queue was longer than three
          seconds, which at a sixty-second interval is *every request after the first* — the
          daily pass would have quietly bled work.

        ``max_wait_s=None`` selects the background contract: wait for the slot, and wait out a
        source cooldown too, rechecking rather than giving up.
        """

        self._ensure_deadline(url)
        key = throttle_key(url)
        while True:
            self._ensure_deadline(url)
            decision = self.reserve(url)
            if decision.allowed:
                break

            assert decision.reason is not None
            wait_s = self._wait_for(decision, key)
            if wait_s is None:  # bounded caller: this is a refusal, not a pause
                raise PolitenessRefused(decision.reason, url)

            remaining = self._remaining()
            if remaining is not None and wait_s + _SEND_SAFETY_MARGIN_S >= remaining:
                raise PolitenessRefused("deadline_exceeded", url)
            self._sleep(wait_s + _SEND_SAFETY_MARGIN_S)

        # Re-read the cooldown immediately before the send: it may have been installed by another
        # process while we were queuing.
        self._ensure_deadline(url)
        if self._is_blocked(key):
            if self.max_wait_s is None:
                self._sleep(_COOLDOWN_POLL_S)
                return self.pre_request(url)
            raise PolitenessRefused("source_cooldown", url)
        self._ensure_deadline(url)

    def _wait_for(self, decision: ReservationDecision, key: str) -> float | None:
        """How long to pause before retrying, or None if this caller must refuse instead."""
        if decision.reason == "source_cooldown":
            # Unbounded callers wait a blocked source out; bounded ones cannot.
            return _COOLDOWN_POLL_S if self.max_wait_s is None else None
        if self.max_wait_s is None:
            return decision.wait_s
        return decision.wait_s if decision.wait_s <= self.max_wait_s else None

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
        domain = throttle_key(refused_url)
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


# --- attaching the policy to a fetcher --------------------------------------

#: Set to "0"/"false" to run a fetcher on the per-instance limiter alone. Only for a machine with
#: no database; it removes the cross-process budget, so two streams double the observed rate.
SHARED_LIMITER_ENV = "HORSERACING_SHARED_LIMITER"


class NoSharedLimiter(RuntimeError):
    """Raised instead of scraping without the machine-wide budget."""


def background_policy(
    *,
    min_interval_s: float,
    database_url: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RequestPoliteness | None:
    """The policy for a caller with no deadline: the daily ingest pass and the backfill script.

    ``max_wait_s=None`` is the whole point. A bounded three-second wait against a sixty-second
    interval refuses *every request after the first*, so an ingest pass wired to the bounded
    contract would not be polite — it would be silently empty.

    FAILS CLOSED. If there is no database to coordinate through we raise rather than quietly
    handing back an uncoordinated fetcher — "the limiter silently did not apply" is the entire
    bug this module exists to fix, and a soft fallback would just add a seventh way for it to
    happen. Set ``HORSERACING_SHARED_LIMITER=0`` to scrape without coordination on purpose.

    (A database that is reachable now but drops later also fails closed, from a different place:
    ``reserve()`` raises out of ``pre_request`` and the fetch does not go out.)
    """
    if os.environ.get(SHARED_LIMITER_ENV, "").strip().lower() in {"0", "false", "no"}:
        return None
    from horseracing_db.session import create_db_engine

    try:
        engine = create_db_engine(database_url)
    except Exception as exc:
        raise NoSharedLimiter(
            "cannot reach the shared request limiter, so the machine-wide netkeiba budget "
            "cannot be enforced. Set DATABASE_URL, or set "
            f"{SHARED_LIMITER_ENV}=0 to scrape uncoordinated on purpose."
        ) from exc
    return RequestPoliteness.for_engine(
        engine, min_interval_s=min_interval_s, max_wait_s=None, sleep=sleep
    )
