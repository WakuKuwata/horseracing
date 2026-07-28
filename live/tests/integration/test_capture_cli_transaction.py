"""CLI commits an invalidation-only capture run (T034)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosSnapshot, RaceHorse
from sqlalchemy import select

from horseracing_live import cli

from tests._capture_support import (
    CAPTURE_POLICY_VERSION,
    RACE_ID,
    artifact_with_horizon,
    seed_race,
)

pytestmark = pytest.mark.integration


class _NoFetchDelegate:
    def get(self, _url: str, *, use_cache: bool = True) -> str:
        raise AssertionError("an existing snapshot must prevent HTTP fetch")


def test_cli_commits_when_the_only_change_is_voiding_a_stale_snapshot(
    session,
    database_url,
    monkeypatch,
):
    now = datetime.datetime.now(datetime.UTC)
    seed_race(
        session,
        race_date=now.date(),
        post_time=now + datetime.timedelta(hours=1),
    )
    field = [
        {
            "horse_id": f"H{number:02d}",
            "horse_number": number,
            "popularity": number,
            "odds": str(2.0 + number),
        }
        for number in range(1, 5)
    ]
    session.add(
        ChaosSnapshot(
            race_id=RACE_ID,
            captured_at=now - datetime.timedelta(minutes=1),
            source="fixture-adapter",
            capture_trigger="explicit_command",
            capture_policy_version=CAPTURE_POLICY_VERSION,
            seconds_to_post=3_660,
            capture_strength="confirmatory",
            field=field,
            n=4,
            content_digest="fixture-cli-void",
            status="active",
        )
    )
    session.flush()
    scratched = session.get(RaceHorse, (RACE_ID, "H04"))
    assert scratched is not None
    scratched.entry_status = EntryStatus.CANCELLED
    session.commit()

    monkeypatch.setattr(
        "horseracing_live.chaos_capture.load_current_chaos_artifact",
        lambda _target_date: artifact_with_horizon(),
    )
    monkeypatch.setattr(
        "horseracing_live.chaos_politeness.make_capture_fetcher",
        lambda **_kwargs: _NoFetchDelegate(),
    )

    assert (
        cli.main(
            [
                "capture-chaos",
                "--race-id",
                RACE_ID,
                "--database-url",
                database_url,
            ]
        )
        == 0
    )

    session.expire_all()
    snapshot = session.scalar(
        select(ChaosSnapshot).where(ChaosSnapshot.race_id == RACE_ID)
    )
    assert snapshot is not None
    assert snapshot.status == "void"
    assert snapshot.void_reason == "field_changed"
