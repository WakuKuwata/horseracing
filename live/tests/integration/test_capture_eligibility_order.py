"""Canonical pre-fetch eligibility ordering for Feature 086 (T027/T027a/T027b)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import ChaosSnapshot
from horseracing_probability.chaos_artifact import ChaosArtifactUnavailableError
from horseracing_probability.chaos_eligibility import confirmation_eligible
from sqlalchemy import select, text
from sqlalchemy.orm import Session

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


def _attempt(session, spy, artifact, **overrides):
    kwargs = {
        "race_id": RACE_ID,
        "fetcher": spy,
        "artifact": artifact,
        "capture_trigger": CAPTURE_TRIGGER,
        "capture_policy_version": CAPTURE_POLICY_VERSION,
        "deadline": float("inf"),
        "clock": lambda: CAPTURED_AT,
    }
    kwargs.update(overrides)
    return capture_chaos(session, **kwargs)


@pytest.mark.parametrize(
    ("race_id", "reason"),
    [
        ("not-a-race", "invalid_race_id"),
        ("202607269999", "race_not_found"),
    ],
)
def test_invalid_or_missing_race_is_rejected_without_fetch(
    session, spy_fetcher, race_id, reason
):
    spy = spy_fetcher(odds_payload())
    report = _attempt(
        session,
        spy,
        artifact_with_horizon(),
        race_id=race_id,
    )
    assert (report.status, report.reason) == ("rejected", reason)
    assert spy.calls == 0


def test_missing_race_date_is_rejected_without_fetch(session, spy_fetcher):
    seed_race(session, race_date=None)
    spy = spy_fetcher(odds_payload())
    report = _attempt(session, spy, artifact_with_horizon())
    assert (report.status, report.reason) == ("rejected", "race_date_unknown")
    assert spy.calls == 0


def test_incomplete_entries_are_rejected_without_fetch(session, spy_fetcher):
    seed_race(session, n_started=0, n_cancelled=0)
    spy = spy_fetcher(odds_payload())
    report = _attempt(session, spy, artifact_with_horizon())
    assert (report.status, report.reason) == ("rejected", "entries_incomplete")
    assert spy.calls == 0


def test_settled_result_is_skipped_without_fetch(session, spy_fetcher):
    seed_race(session)
    settle_race(session)
    spy = spy_fetcher(odds_payload())
    report = _attempt(session, spy, artifact_with_horizon())
    assert (report.status, report.reason) == ("skipped", "result_settled")
    assert spy.calls == 0


def test_unknown_post_time_is_skipped_without_fetch(session, spy_fetcher):
    seed_race(session, post_time=None)
    spy = spy_fetcher(odds_payload())
    report = _attempt(session, spy, artifact_with_horizon())
    assert (report.status, report.reason) == ("skipped", "post_time_unknown")
    assert spy.calls == 0


def test_elapsed_post_time_is_skipped_without_fetch(session, spy_fetcher):
    seed_race(session, post_time=CAPTURED_AT)
    spy = spy_fetcher(odds_payload())
    report = _attempt(session, spy, artifact_with_horizon())
    assert (report.status, report.reason) == ("skipped", "post_time_elapsed")
    assert spy.calls == 0


def test_operational_floor_is_checked_without_fetch(session, spy_fetcher):
    seed_race(session, post_time=CAPTURED_AT + datetime.timedelta(seconds=599))
    spy = spy_fetcher(odds_payload())
    report = _attempt(
        session,
        spy,
        artifact_with_horizon(),
        min_seconds_to_post=600,
    )
    assert (report.status, report.reason) == ("skipped", "min_seconds_to_post")
    assert spy.calls == 0


def test_operational_floor_defaults_to_zero(session, spy_fetcher):
    seed_race(session, post_time=CAPTURED_AT + datetime.timedelta(seconds=1))
    spy = spy_fetcher(odds_payload())
    report = _attempt(session, spy, artifact_with_horizon(minimum=0))
    assert report.status == "captured"
    assert spy.calls == 1


def test_artifact_failure_is_after_time_gates_but_before_fetch(session, spy_fetcher):
    seed_race(session)
    spy = spy_fetcher(odds_payload())

    def unavailable(_target_date):
        raise ChaosArtifactUnavailableError("fixture artifact unavailable")

    report = _attempt(
        session,
        spy,
        None,
        artifact_loader=unavailable,
    )
    assert (report.status, report.reason) == ("rejected", "artifact_unavailable")
    assert spy.calls == 0


def test_operational_floor_precedes_artifact_loading(session, spy_fetcher):
    seed_race(session, post_time=CAPTURED_AT + datetime.timedelta(seconds=599))
    spy = spy_fetcher(odds_payload())

    def unavailable(_target_date):
        raise ChaosArtifactUnavailableError("must not be reached")

    report = _attempt(
        session,
        spy,
        None,
        artifact_loader=unavailable,
        min_seconds_to_post=600,
    )
    assert (report.status, report.reason) == ("skipped", "min_seconds_to_post")
    assert spy.calls == 0


@pytest.mark.parametrize(
    ("n_started", "n_cancelled", "reason"),
    [
        (0, 4, "no_started_horses"),
        (3, 0, "field_too_small"),
    ],
)
def test_field_size_gates_are_skipped_without_fetch(
    session, spy_fetcher, n_started, n_cancelled, reason
):
    seed_race(session, n_started=n_started, n_cancelled=n_cancelled)
    spy = spy_fetcher(odds_payload(max(n_started, 4)))
    report = _attempt(session, spy, artifact_with_horizon())
    assert (report.status, report.reason) == ("skipped", reason)
    assert spy.calls == 0


@pytest.mark.parametrize("status", ["active", "void"])
def test_existing_row_is_skipped_without_fetch(session, spy_fetcher, status):
    seed_race(session)
    artifact = artifact_with_horizon()
    first_spy = spy_fetcher(odds_payload())
    first = _attempt(session, first_spy, artifact)
    assert first.status == "captured"
    snapshot = session.get(ChaosSnapshot, first.chaos_snapshot_id)
    assert snapshot is not None
    snapshot.status = status
    snapshot.void_reason = "field_changed" if status == "void" else None
    session.flush()

    spy = spy_fetcher(odds_payload(odds_offset=100.0))
    report = _attempt(session, spy, artifact)
    assert (report.status, report.reason) == ("skipped", "already_captured")
    assert spy.calls == 0


def test_predict_auto_outside_horizon_precedes_existing_row_check(session, spy_fetcher):
    post_time = CAPTURED_AT + datetime.timedelta(hours=5)
    seed_race(session, post_time=post_time)
    artifact = artifact_with_horizon(maximum=3_600)
    first_spy = spy_fetcher(odds_payload())
    first = _attempt(
        session,
        first_spy,
        artifact,
        capture_trigger="explicit_command",
    )
    assert first.status == "captured"
    assert first_spy.calls == 1

    spy = spy_fetcher(odds_payload(odds_offset=100.0))
    report = _attempt(
        session,
        spy,
        artifact,
        capture_trigger="predict_auto",
    )
    assert (report.status, report.reason) == ("skipped", "outside_primary_horizon")
    assert spy.calls == 0


@pytest.mark.parametrize(
    "trigger",
    ["predict_manual", "daily_operational", "explicit_command"],
)
def test_display_only_triggers_capture_outside_primary_horizon(
    session, spy_fetcher, trigger
):
    post_time = CAPTURED_AT + datetime.timedelta(hours=5)
    seed_race(session, post_time=post_time)
    artifact = artifact_with_horizon(maximum=3_600)
    spy = spy_fetcher(odds_payload())
    report = _attempt(
        session,
        spy,
        artifact,
        capture_trigger=trigger,
    )
    assert report.status == "captured"
    assert spy.calls == 1

    snapshot = session.scalar(
        select(ChaosSnapshot).where(ChaosSnapshot.race_id == RACE_ID)
    )
    assert snapshot is not None
    started_now = [
        (row["horse_id"], row["horse_number"])
        for row in snapshot.field
    ]
    assert confirmation_eligible(snapshot, artifact, started_now) is False


def test_predict_auto_outside_primary_horizon_skips_without_fetch(session, spy_fetcher):
    seed_race(session, post_time=CAPTURED_AT + datetime.timedelta(hours=5))
    spy = spy_fetcher(odds_payload())
    report = _attempt(
        session,
        spy,
        artifact_with_horizon(maximum=3_600),
        capture_trigger="predict_auto",
    )
    assert (report.status, report.reason) == ("skipped", "outside_primary_horizon")
    assert spy.calls == 0


def test_predict_auto_outside_horizon_does_not_try_the_capture_lock(
    session,
    engine,
    spy_fetcher,
):
    seed_race(session, post_time=CAPTURED_AT + datetime.timedelta(hours=5))
    session.commit()
    with Session(engine) as lock_holder:
        lock_holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"chaos-capture:{RACE_ID}"},
        )
        spy = spy_fetcher(odds_payload())
        report = _attempt(
            session,
            spy,
            artifact_with_horizon(maximum=3_600),
            capture_trigger="predict_auto",
        )
        assert (report.status, report.reason) == (
            "skipped",
            "outside_primary_horizon",
        )
        assert spy.calls == 0
        lock_holder.rollback()


def test_result_settled_precedes_post_time_and_artifact_failures(session, spy_fetcher):
    seed_race(session, post_time=None)
    settle_race(session)
    spy = spy_fetcher(odds_payload())

    def unavailable(_target_date):
        raise ChaosArtifactUnavailableError("must not be reached")

    report = _attempt(session, spy, None, artifact_loader=unavailable)
    assert (report.status, report.reason) == ("skipped", "result_settled")
    assert spy.calls == 0


def test_post_time_elapsed_precedes_artifact_failure(session, spy_fetcher):
    seed_race(session, post_time=CAPTURED_AT)
    spy = spy_fetcher(odds_payload())

    def unavailable(_target_date):
        raise ChaosArtifactUnavailableError("must not be reached")

    report = _attempt(session, spy, None, artifact_loader=unavailable)
    assert (report.status, report.reason) == ("skipped", "post_time_elapsed")
    assert spy.calls == 0
