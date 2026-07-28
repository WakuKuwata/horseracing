"""The pre-fetch try-lock collapses concurrent captures to one HTTP call (T029)."""

from __future__ import annotations

import threading

import pytest
from horseracing_db.models import ChaosSnapshot
from sqlalchemy import func, select
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
)

pytestmark = pytest.mark.integration


def test_two_sessions_make_one_fetch_and_one_active_row(session, engine, spy_fetcher):
    seed_race(session)
    session.commit()
    artifact = artifact_with_horizon()
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    reports = {}
    errors: list[BaseException] = []

    def block_winner_fetch() -> None:
        fetch_started.set()
        if not release_fetch.wait(timeout=10):
            raise TimeoutError("test did not release winner fetch")

    winner_spy = spy_fetcher(odds_payload(), on_get=block_winner_fetch)
    loser_spy = spy_fetcher(odds_payload(odds_offset=100.0))

    def run_capture(name, spy) -> None:
        try:
            with Session(engine) as worker_session:
                reports[name] = capture_chaos(
                    worker_session,
                    race_id=RACE_ID,
                    fetcher=spy,
                    artifact=artifact,
                    capture_trigger=CAPTURE_TRIGGER,
                    capture_policy_version=CAPTURE_POLICY_VERSION,
                    deadline=float("inf"),
                    clock=lambda: CAPTURED_AT,
                )
                if reports[name].captured or reports[name].voided:
                    worker_session.commit()
                else:
                    worker_session.rollback()
        except BaseException as exc:  # pragma: no cover - surfaced in the parent thread
            errors.append(exc)

    winner = threading.Thread(target=run_capture, args=("winner", winner_spy))
    loser = threading.Thread(target=run_capture, args=("loser", loser_spy))
    winner.start()
    assert fetch_started.wait(timeout=10), "winner never reached the fetch"
    loser.start()
    loser.join(timeout=10)
    assert not loser.is_alive(), "loser queued instead of returning from the try-lock"
    release_fetch.set()
    winner.join(timeout=10)
    assert not winner.is_alive()
    assert errors == []

    assert reports["winner"].status == "captured"
    assert (reports["loser"].status, reports["loser"].reason) == (
        "skipped",
        "concurrent_capture",
    )
    assert winner_spy.calls == 1
    assert loser_spy.calls == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == RACE_ID)
        )
        == 1
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == RACE_ID)
            .where(ChaosSnapshot.status == "active")
        )
        == 1
    )
