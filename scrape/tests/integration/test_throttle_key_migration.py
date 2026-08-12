"""093: what happens to throttle rows written under the OLD per-hostname key.

Migration 0016 folds them into one row per source. The interesting case is not the happy path —
it is what gets *lost* if the merge is naive:

  * a cooldown recorded under ``https://race.netkeiba.com`` while the source is actively blocking
    us. Rename the key without moving it and the first request after deploy goes straight into a
    block, which is the precise failure the cooldown exists to prevent;
  * an interval that has not elapsed yet, which becomes a free request.

So the merge keeps the STRICTEST value it finds, not the newest.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import FetchThrottleState
from sqlalchemy import select, text

from horseracing_scrape.politeness import RequestPoliteness, throttle_key

pytestmark = pytest.mark.integration

URL = "https://race.netkeiba.com/race/result.html?race_id=1"
KEY = "netkeiba.com"

def _run_merge(session) -> None:
    """Run 0016's statements against this session.

    Reading the SQL out of the migration module keeps the test honest: it exercises the shipped
    statements rather than a paraphrase that could drift from them.
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[3] / (
        "db/migrations/versions/0016_throttle_key_merge.py"
    )
    session.flush()  # the migration reads rows, so the ORM inserts must have landed
    body = src.read_text()
    statements = re.findall(r'op\.execute\(\s*f?"""(.*?)"""\s*\)', body, re.S)
    assert len(statements) == 2, "0016 should be exactly the merge and the delete"
    for stmt in statements:
        session.execute(text(stmt.replace("{_SOURCE}", KEY).replace("%%", "%")))


def _row(session, domain: str, *, next_allowed_at=None, blocked_until=None, reason=None):
    session.add(
        FetchThrottleState(
            domain=domain,
            next_allowed_at=next_allowed_at,
            blocked_until=blocked_until,
            block_reason=reason,
            updated_at=datetime.datetime.now(datetime.UTC),
        )
    )


def _now():
    return datetime.datetime.now(datetime.UTC)


def test_an_active_cooldown_survives_the_rekey(session):
    """The one that actually matters. Losing this means resuming against a blocking source."""
    blocked_until = _now() + datetime.timedelta(minutes=30)
    with session.begin():
        _row(session, "https://race.netkeiba.com", blocked_until=blocked_until,
             reason="http_400", next_allowed_at=_now())
        _run_merge(session)

    row = session.get(FetchThrottleState, KEY)
    assert row is not None
    assert row.blocked_until == blocked_until
    assert row.block_reason == "http_400"


def test_the_strictest_of_several_hosts_wins(session):
    near = _now() + datetime.timedelta(seconds=10)
    far = _now() + datetime.timedelta(minutes=45)
    with session.begin():
        _row(session, "https://race.netkeiba.com", next_allowed_at=near)
        _row(session, "https://db.netkeiba.com", next_allowed_at=far,
             blocked_until=far, reason="http_429")
        _run_merge(session)

    row = session.get(FetchThrottleState, KEY)
    assert row.next_allowed_at == far, "the furthest-future restriction must win"
    assert row.blocked_until == far
    assert row.block_reason == "http_429"


def test_the_hostname_rows_are_retired(session):
    """Left behind, they are dead weight that a mixed-version process would keep writing to."""
    with session.begin():
        _row(session, "https://race.netkeiba.com", next_allowed_at=_now())
        _row(session, "https://db.netkeiba.com", next_allowed_at=_now())
        _run_merge(session)

    assert [r.domain for r in session.scalars(select(FetchThrottleState))] == [KEY]


def test_an_existing_canonical_row_is_not_weakened(session):
    """Re-running must never hand out a slot: the merge takes GREATEST, not the incoming value."""
    strict = _now() + datetime.timedelta(minutes=50)
    with session.begin():
        _row(session, KEY, next_allowed_at=strict, blocked_until=strict, reason="http_403")
        _row(session, "https://race.netkeiba.com", next_allowed_at=_now())
        _run_merge(session)

    row = session.get(FetchThrottleState, KEY)
    assert row.next_allowed_at == strict
    assert row.blocked_until == strict


def test_running_it_twice_changes_nothing(session):
    blocked = _now() + datetime.timedelta(minutes=30)
    with session.begin():
        _row(session, "https://race.netkeiba.com", blocked_until=blocked, reason="http_400",
             next_allowed_at=blocked)
        _run_merge(session)
        _run_merge(session)

    rows = list(session.scalars(select(FetchThrottleState)))
    assert [r.domain for r in rows] == [KEY]
    assert rows[0].blocked_until == blocked


def test_unrelated_sources_are_left_alone(session):
    with session.begin():
        _row(session, "https://example.org", next_allowed_at=_now())
        _run_merge(session)

    assert sorted(r.domain for r in session.scalars(select(FetchThrottleState))) == [
        "https://example.org"
    ]


def test_the_merged_row_is_the_one_the_runtime_reads(session_factory, session):
    """The migration and `throttle_key()` must agree, or the merge writes a row nobody reads."""
    blocked = _now() + datetime.timedelta(minutes=30)
    with session.begin():
        _row(session, "https://race.netkeiba.com", blocked_until=blocked, reason="http_400",
             next_allowed_at=blocked)
        _run_merge(session)

    assert throttle_key(URL) == KEY
    policy = RequestPoliteness(session_factory, min_interval_s=60.0, sleep=lambda _s: None)
    decision = policy.reserve(URL)
    assert decision.allowed is False
    assert decision.reason == "source_cooldown", "the inherited cooldown must be observed"
