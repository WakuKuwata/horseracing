"""One observation for the lifetime of a race (T028/T028a)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosReadout, ChaosSnapshot, RaceHorse
from horseracing_probability.chaos_eligibility import (
    confirmation_eligible,
    display_eligible,
)
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


def _capture(session, spy, artifact):
    return capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy,
        artifact=artifact,
        capture_trigger=CAPTURE_TRIGGER,
        capture_policy_version=CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: CAPTURED_AT,
    )


def test_second_attempt_never_fetches_or_adds_an_odds_row(session, spy_fetcher):
    seed_race(session)
    artifact = artifact_with_horizon()
    first_spy = spy_fetcher(odds_payload())
    assert _capture(session, first_spy, artifact).status == "captured"
    session.commit()

    second_spy = spy_fetcher(odds_payload(odds_offset=50.0))
    second = _capture(session, second_spy, artifact)

    assert (second.status, second.reason) == ("skipped", "already_captured")
    assert second_spy.calls == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == RACE_ID)
        )
        == 1
    )
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1


def test_field_a_to_b_to_a_voids_monotonically_without_recapture(session, spy_fetcher):
    seed_race(session)
    artifact = artifact_with_horizon()
    first_spy = spy_fetcher(odds_payload())
    first = _capture(session, first_spy, artifact)
    assert first.status == "captured"
    session.commit()

    horse = session.get(RaceHorse, (RACE_ID, "H04"))
    assert horse is not None
    horse.entry_status = EntryStatus.CANCELLED
    session.commit()

    field_b_spy = spy_fetcher(odds_payload(3, odds_offset=10.0))
    field_b = _capture(session, field_b_spy, artifact)
    assert (field_b.status, field_b.reason) == ("skipped", "already_captured")
    assert field_b.voided is True
    assert field_b_spy.calls == 0
    session.commit()

    horse = session.get(RaceHorse, (RACE_ID, "H04"))
    assert horse is not None
    horse.entry_status = EntryStatus.STARTED
    session.commit()

    field_a_again_spy = spy_fetcher(odds_payload(odds_offset=20.0))
    field_a_again = _capture(session, field_a_again_spy, artifact)
    assert (field_a_again.status, field_a_again.reason) == (
        "skipped",
        "already_captured",
    )
    assert field_a_again.voided is False
    assert field_a_again_spy.calls == 0

    snapshot = session.scalar(
        select(ChaosSnapshot).where(ChaosSnapshot.race_id == RACE_ID)
    )
    assert snapshot is not None
    assert snapshot.status == "void"
    assert snapshot.void_reason == "field_changed"
    started_now = [
        (horse_id, horse_number)
        for horse_id, horse_number in session.execute(
            select(RaceHorse.horse_id, RaceHorse.horse_number)
            .where(RaceHorse.race_id == RACE_ID)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        )
    ]
    assert display_eligible(snapshot, started_now) is False
    assert confirmation_eligible(snapshot, artifact, started_now) is False
    assert (
        session.scalar(
            select(func.count())
            .select_from(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == RACE_ID)
        )
        == 1
    )
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1
