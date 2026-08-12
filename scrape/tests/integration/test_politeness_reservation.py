"""093: the reservation itself, against a real PostgreSQL.

The interesting behaviour lives in `clock_timestamp()` + `SELECT ... FOR UPDATE`, so this cannot
be faked. The property that matters most is the one a unit test cannot express: a caller that
wakes up LATE must not still be holding a valid claim.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import FetchThrottleState
from sqlalchemy import select

from horseracing_scrape.politeness import RequestPoliteness, throttle_key

pytestmark = pytest.mark.integration

RACE_URL = "https://race.netkeiba.com/race/result.html?race_id=202601010601"
DB_URL = "https://db.netkeiba.com/horse/2022103995/"
KEY = "netkeiba.com"


def _politeness(session_factory, **kw) -> RequestPoliteness:
    kw.setdefault("min_interval_s", 60.0)
    kw.setdefault("sleep", lambda _s: None)
    return RequestPoliteness(session_factory, **kw)


def test_the_first_request_is_due_immediately(session_factory):
    p = _politeness(session_factory)
    assert p.reserve(RACE_URL).allowed is True


def test_the_second_request_is_not_due_and_is_not_booked(session_factory):
    """The old shape granted the second caller a FUTURE slot and let it sleep into it. Now the
    slot is simply not due, and nothing is reserved on its behalf."""
    p = _politeness(session_factory)
    assert p.reserve(RACE_URL).allowed is True

    second = p.reserve(RACE_URL)
    assert second.allowed is False
    assert second.reason == "throttle_backlog"
    assert 0 < second.wait_s <= 60.0

    with session_factory() as s:
        row = s.scalar(select(FetchThrottleState).where(FetchThrottleState.domain == KEY))
        first_due = row.next_allowed_at

    # A refused attempt must not push the slot further out — otherwise a polling caller
    # starves itself by repeatedly deferring its own turn.
    p.reserve(RACE_URL)
    with session_factory() as s:
        row = s.scalar(select(FetchThrottleState).where(FetchThrottleState.domain == KEY))
    assert row.next_allowed_at == first_due


def test_a_slot_is_never_granted_for_the_future(session_factory):
    """THE invariant that closes the burst hazard.

    The old shape granted a caller whose turn was up to `max_wait_s` away, advanced the row, and
    let it sleep into its booked instant. Two such holders (say T+60 and T+120) that both wake
    late — laptop resume — then send seconds apart under a sixty-second contract.

    A generous `max_wait_s` is what makes this test able to fail: with the old code the second
    call is granted with `wait_s≈60`; with claim-when-due it is refused. Asserting
    "allowed implies wait_s == 0" states the invariant directly, independent of the bound.
    """
    p = _politeness(session_factory, max_wait_s=120.0)
    first = p.reserve(RACE_URL)
    assert first.allowed is True and first.wait_s == 0.0

    second = p.reserve(RACE_URL)
    assert second.allowed is False, (
        "a caller whose turn is 60s away must not hold a claim while it sleeps"
    )
    assert second.wait_s > 0


def test_a_refused_caller_does_not_move_the_slot(session_factory):
    """A polling background caller must not defer its own turn every time it checks."""
    p = _politeness(session_factory, max_wait_s=120.0)
    p.reserve(RACE_URL)
    with session_factory() as s:
        due = s.scalar(select(FetchThrottleState).where(FetchThrottleState.domain == KEY)
                       ).next_allowed_at
    for _ in range(3):
        assert p.reserve(RACE_URL).allowed is False
    with session_factory() as s:
        assert s.scalar(select(FetchThrottleState).where(FetchThrottleState.domain == KEY)
                        ).next_allowed_at == due


def test_the_slot_reopens_once_the_interval_has_elapsed(session_factory):
    p = _politeness(session_factory, max_wait_s=120.0)
    assert p.reserve(RACE_URL).allowed is True
    with session_factory() as s, s.begin():
        row = s.scalar(select(FetchThrottleState).where(FetchThrottleState.domain == KEY))
        row.next_allowed_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    assert p.reserve(RACE_URL).allowed is True


def test_both_netkeiba_hosts_contend_for_the_same_slot(session_factory):
    """race.* and db.* are one operator; a per-host bucket doubled the observed rate."""
    p = _politeness(session_factory)
    assert p.reserve(RACE_URL).allowed is True
    assert p.reserve(DB_URL).allowed is False, "db.netkeiba must not get its own budget"

    with session_factory() as s:
        keys = list(s.scalars(select(FetchThrottleState.domain)))
    assert keys == [KEY], f"one row for the whole source, got {keys}"


def test_separate_policy_objects_share_the_row(session_factory):
    """The ops worker builds a fetcher per loop iteration and capture builds its own. Politeness
    has to survive that: the state is in the database, not in the object."""
    assert _politeness(session_factory).reserve(RACE_URL).allowed is True
    assert _politeness(session_factory).reserve(RACE_URL).allowed is False


def test_a_weak_caller_cannot_shorten_the_interval(session_factory, monkeypatch):
    from horseracing_scrape.politeness import SOURCE_INTERVAL_ENV

    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "60")
    weak = _politeness(session_factory, min_interval_s=1.0)  # capture's old default
    assert weak.reserve(RACE_URL).allowed is True

    with session_factory() as s:
        row = s.scalar(select(FetchThrottleState).where(FetchThrottleState.domain == KEY))
        gap = (row.next_allowed_at - datetime.datetime.now(datetime.UTC)).total_seconds()
    assert gap > 30, f"the source contract must win, next slot only {gap:.1f}s away"


def test_a_cooldown_blocks_and_bounded_callers_refuse(session_factory):
    from horseracing_scrape.fetch import FetchRefused
    from horseracing_scrape.politeness import PolitenessRefused

    p = _politeness(session_factory, max_wait_s=3.0)
    p.record_refusal(FetchRefused(400, RACE_URL))  # netkeiba's real block status

    decision = p.reserve(RACE_URL)
    assert decision.allowed is False
    assert decision.reason == "source_cooldown"
    with pytest.raises(PolitenessRefused, match="source_cooldown"):
        p.pre_request(RACE_URL)


def test_a_cooldown_on_one_host_blocks_the_other(session_factory):
    """A refusal is about the SOURCE. Recording it per-host would let the daily job keep hitting
    db.netkeiba while race.netkeiba is blocking us."""
    from horseracing_scrape.fetch import FetchRefused

    p = _politeness(session_factory)
    p.record_refusal(FetchRefused(429, DB_URL))
    assert p.reserve(RACE_URL).reason == "source_cooldown"
    assert throttle_key(DB_URL) == throttle_key(RACE_URL)
