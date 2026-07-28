"""Fetch-time races are classified separately and persist nothing (T028a/T028b)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosReadout, ChaosSnapshot, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_live.chaos_capture import capture_chaos

from tests._capture_support import (
    CAPTURED_AT,
    CAPTURE_POLICY_VERSION,
    CAPTURE_TRIGGER,
    RACE_ID,
    artifact_with_horizon,
    odds_payload,
    seed_race,
    settle_race,
)

pytestmark = pytest.mark.integration


def _assert_no_capture_rows(session) -> None:
    assert session.scalar(select(func.count()).select_from(ChaosSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 0


def _attempt(session, spy, *, clock=lambda: CAPTURED_AT, minimum=0):
    return capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact_with_horizon(),
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        min_seconds_to_post=minimum,
        clock=clock,
    )


def test_result_settles_during_fetch(session, spy_fetcher):
    seed_race(session)
    spy = spy_fetcher(odds_payload(), on_get=lambda: settle_race(session))

    report = _attempt(session, spy)

    assert (report.status, report.reason) == (
        "skipped",
        "result_settled_during_fetch",
    )
    assert spy.calls == 1
    _assert_no_capture_rows(session)


def test_post_time_elapses_during_fetch(session, spy_fetcher):
    seed_race(session)

    def elapse_post_time() -> None:
        race = session.get(Race, RACE_ID)
        assert race is not None
        race.post_time = CAPTURED_AT
        session.flush()

    spy = spy_fetcher(odds_payload(), on_get=elapse_post_time)
    report = _attempt(session, spy)

    assert (report.status, report.reason) == (
        "skipped",
        "post_time_elapsed_during_fetch",
    )
    assert spy.calls == 1
    _assert_no_capture_rows(session)


def test_operational_floor_is_crossed_during_fetch(session, spy_fetcher):
    seed_race(session)
    times = iter(
        (
            CAPTURED_AT,
            CAPTURED_AT + datetime.timedelta(seconds=1_201),
        )
    )
    spy = spy_fetcher(odds_payload())

    report = _attempt(
        session,
        spy,
        clock=lambda: next(times),
        minimum=600,
    )

    assert (report.status, report.reason) == (
        "skipped",
        "min_seconds_to_post_during_fetch",
    )
    assert spy.calls == 1
    _assert_no_capture_rows(session)


def test_field_changes_during_fetch(session, spy_fetcher):
    seed_race(session)

    def cancel_one_horse() -> None:
        horse = session.get(RaceHorse, (RACE_ID, "H04"))
        assert horse is not None
        horse.entry_status = EntryStatus.CANCELLED
        session.flush()

    spy = spy_fetcher(odds_payload(), on_get=cancel_one_horse)
    report = _attempt(session, spy)

    assert (report.status, report.reason) == (
        "skipped",
        "field_changed_during_fetch",
    )
    assert spy.calls == 1
    _assert_no_capture_rows(session)
