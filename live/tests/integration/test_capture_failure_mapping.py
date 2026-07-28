"""Feature 086 capture converts fetch-layer failures into typed outcomes."""

from __future__ import annotations

import pytest
from horseracing_scrape.fetch import FetchError, FetchRefused, RobotsDisallowed

from horseracing_live.chaos_capture import capture_chaos

from tests._capture_support import (
    CAPTURED_AT,
    CAPTURE_POLICY_VERSION,
    CAPTURE_TRIGGER,
    RACE_ID,
    artifact_with_horizon,
    seed_race,
)

pytestmark = pytest.mark.integration


class _RaisingFetcher:
    source = "fixture-adapter"

    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    def get(self, _url: str, *, use_cache: bool = True) -> str:
        assert use_cache is False
        self.calls += 1
        raise self.error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            FetchRefused(429, "https://race.netkeiba.com/odds"),
            ("skipped", "source_cooldown"),
        ),
        (
            RobotsDisallowed("https://race.netkeiba.com/odds"),
            ("skipped", "robots_disallowed"),
        ),
        (
            FetchError("connection failed"),
            ("failed", "fetch_failed"),
        ),
    ],
)
def test_fetch_exceptions_do_not_escape_capture(session, error, expected):
    seed_race(session)
    fetcher = _RaisingFetcher(error)

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=fetcher,
        artifact=artifact_with_horizon(),
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: CAPTURED_AT,
    )

    assert (report.status, report.reason) == expected
    assert fetcher.calls == 1


def test_bad_odds_payload_is_failed_fetch_failed(session, spy_fetcher):
    seed_race(session)
    spy = spy_fetcher("not-json")

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact_with_horizon(),
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: CAPTURED_AT,
    )

    assert (report.status, report.reason) == ("failed", "fetch_failed")
