"""Shared deterministic fixtures for Feature 086 capture integration tests."""

from __future__ import annotations

import datetime
import json
from dataclasses import replace
from decimal import Decimal

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Race, RaceHorse, RaceResult

from horseracing_live.chaos_capture import load_current_chaos_artifact

RACE_ID = "202607260101"
RACE_DATE = datetime.date(2026, 7, 26)
CAPTURED_AT = datetime.datetime(2026, 7, 26, 5, 30, tzinfo=datetime.UTC)
POST_TIME = datetime.datetime(2026, 7, 26, 6, 0, tzinfo=datetime.UTC)
CAPTURE_TRIGGER = "explicit_command"
CAPTURE_POLICY_VERSION = "capture_policy_v1"


def odds_payload(n: int = 4, *, odds_offset: float = 0.0) -> str:
    rows = {
        f"{number:02d}": [
            str(1.5 + odds_offset + number),
            "0.0",
            str(number),
        ]
        for number in range(1, n + 1)
    }
    return json.dumps({"data": {"odds": {"1": rows}}})


def seed_race(
    session,
    *,
    race_id: str = RACE_ID,
    race_date: datetime.date | None = RACE_DATE,
    post_time: datetime.datetime | None = POST_TIME,
    n_started: int = 4,
    n_cancelled: int = 0,
) -> None:
    session.add(
        Race(
            race_id=race_id,
            race_number=1,
            race_date=race_date,
            post_time=post_time,
        )
    )
    total = n_started + n_cancelled
    for number in range(1, total + 1):
        session.add(Horse(horse_id=f"H{number:02d}", horse_name=f"Horse {number}"))
    session.flush()
    for number in range(1, total + 1):
        session.add(
            RaceHorse(
                race_id=race_id,
                horse_id=f"H{number:02d}",
                horse_number=number,
                odds=Decimal(str(2.0 + number)),
                popularity=number,
                entry_status=(
                    EntryStatus.STARTED if number <= n_started else EntryStatus.CANCELLED
                ),
            )
        )
    session.flush()


def settle_race(session, *, race_id: str = RACE_ID, horse_id: str = "H01") -> None:
    session.add(
        RaceResult(
            race_id=race_id,
            horse_id=horse_id,
            finish_order=1,
            result_status=ResultStatus.FINISHED,
        )
    )
    session.flush()


def artifact_with_horizon(*, minimum: int = 600, maximum: int = 3_600):
    artifact = load_current_chaos_artifact(RACE_DATE)
    preregistration = dict(artifact.preregistration)
    preregistration["primary_horizon"] = {
        "minimum_seconds_to_post": minimum,
        "maximum_seconds_to_post": maximum,
    }
    return replace(artifact, preregistration=preregistration)

