"""Feature 086 MVP happy path (T026a)."""

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


def test_eligible_race_captures_one_confirmatory_observation(session, spy_fetcher):
    seed_race(session)
    artifact = artifact_with_horizon()
    spy = spy_fetcher(odds_payload())

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact,
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: CAPTURED_AT,
    )

    assert report.status == "captured"
    assert report.reason == "ok"
    assert report.capture_strength == "confirmatory"
    assert spy.calls == 1
    assert (
        session.scalar(
            select(func.count())
            .select_from(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == RACE_ID)
            .where(ChaosSnapshot.status == "active")
        )
        == 1
    )
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1
