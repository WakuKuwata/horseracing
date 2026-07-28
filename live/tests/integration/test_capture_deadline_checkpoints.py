"""Feature 086 one monotonic deadline covers fetch, parse/derive, and save."""

from __future__ import annotations

import pytest
from horseracing_db.models import ChaosReadout, ChaosSnapshot
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
)

pytestmark = pytest.mark.integration


def _monotonic_sequence(*values: float):
    remaining = list(values)

    def clock() -> float:
        return remaining.pop(0)

    return clock


def test_deadline_exhausted_before_fetch_sends_zero_requests(session, spy_fetcher):
    seed_race(session)
    spy = spy_fetcher(odds_payload())

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact_with_horizon(),
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=10.0,
        clock=lambda: CAPTURED_AT,
        monotonic_clock=lambda: 10.0,
    )

    assert (report.status, report.reason) == ("skipped", "deadline_exceeded")
    assert spy.calls == 0


def test_deadline_exhausted_before_parse_wins_over_bad_payload(session, spy_fetcher):
    seed_race(session)
    spy = spy_fetcher("not-json")

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact_with_horizon(),
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=10.0,
        clock=lambda: CAPTURED_AT,
        monotonic_clock=_monotonic_sequence(0.0, 10.0),
    )

    assert (report.status, report.reason) == ("skipped", "deadline_exceeded")
    assert spy.calls == 1


def test_deadline_exhausted_before_save_persists_no_rows(session, spy_fetcher):
    seed_race(session)
    spy = spy_fetcher(odds_payload())

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact_with_horizon(),
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=10.0,
        clock=lambda: CAPTURED_AT,
        monotonic_clock=_monotonic_sequence(0.0, 0.0, 0.0, 10.0),
    )

    assert (report.status, report.reason) == ("skipped", "deadline_exceeded")
    assert spy.calls == 1
    assert session.scalar(select(func.count()).select_from(ChaosSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 0
