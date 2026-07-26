"""Feature 084 live capture persistence and frozen-input invariants."""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import (
    ChaosReadout,
    ChaosSnapshot,
    Horse,
    Race,
    RaceHorse,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from horseracing_live.chaos_capture import (
    capture_chaos,
    derive_chaos_readout,
    load_current_chaos_artifact,
)

pytestmark = pytest.mark.integration

_RACE_ID = "202607260101"
_RACE_DATE = datetime.date(2026, 7, 26)
_CAPTURED_AT = datetime.datetime(2026, 7, 26, 5, 30, tzinfo=datetime.UTC)
_POST_TIME = datetime.datetime(2026, 7, 26, 6, 0, tzinfo=datetime.UTC)


class StubFetcher:
    source = "fixture-adapter"

    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[tuple[str, bool]] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.calls.append((url, use_cache))
        return self.payload


def _payload(n: int = 12, *, odds_offset: float = 0.0) -> str:
    rows = {
        f"{number:02d}": [
            str(1.5 + odds_offset + number),
            "0.0",
            str(number),
        ]
        for number in range(1, n + 1)
    }
    return json.dumps({"data": {"odds": {"1": rows}}})


def _seed_pending_race(session, *, n: int = 12) -> None:
    session.add(
        Race(
            race_id=_RACE_ID,
            race_number=1,
            race_date=_RACE_DATE,
            post_time=_POST_TIME,
        )
    )
    for number in range(1, n + 1):
        session.add(Horse(horse_id=f"H{number:02d}", horse_name=f"Horse {number}"))
    # Repository convention: flush the parent race and horses before race_horses.
    session.flush()
    for number in range(1, n + 1):
        session.add(
            RaceHorse(
                race_id=_RACE_ID,
                horse_id=f"H{number:02d}",
                horse_number=number,
                odds=Decimal(str(2.0 + number)),
                popularity=number,
                entry_status=EntryStatus.STARTED,
            )
        )
    session.flush()


def _derived_bytes(value) -> bytes:
    return json.dumps(
        asdict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def test_frozen_field_recomputes_byte_identically_after_race_horses_mutate(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    report = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT,
    )
    assert report.captured
    session.flush()

    snapshot = session.scalar(
        select(ChaosSnapshot).where(ChaosSnapshot.chaos_snapshot_id == report.chaos_snapshot_id)
    )
    readout = session.scalar(
        select(ChaosReadout).where(ChaosReadout.chaos_snapshot_id == report.chaos_snapshot_id)
    )
    assert snapshot is not None and readout is not None
    before = derive_chaos_readout(snapshot.field, artifact)
    before_bytes = _derived_bytes(before)

    horses = list(session.scalars(select(RaceHorse).where(RaceHorse.race_id == _RACE_ID)))
    for index, horse in enumerate(horses, start=1):
        horse.odds = Decimal(str(900 + index))
        horse.popularity = 99
    session.flush()

    after = derive_chaos_readout(snapshot.field, artifact)
    assert _derived_bytes(after) == before_bytes
    assert readout.p_s_ge_20 == Decimal(str(before.p_s_ge_20))
    assert readout.p_himo_are == Decimal(str(before.p_himo_are))
    assert readout.p_total_collapse == Decimal(str(before.p_total_collapse))
    assert readout.raw_p_s_ge_20 == Decimal(str(before.raw_p_s_ge_20))
    assert readout.raw_p_himo_are == Decimal(str(before.raw_p_himo_are))
    assert readout.raw_p_total_collapse == Decimal(str(before.raw_p_total_collapse))
    assert readout.expected_s == Decimal(str(before.expected_s))


def test_result_pending_is_reverified_before_readout_and_both_writes_roll_back(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    states = iter((True, True, False))
    checks = 0

    def pending_check(_session, _race_id):
        nonlocal checks
        checks += 1
        return next(states), "test transition"

    report = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT,
        pending_check=pending_check,
    )

    assert checks == 3
    assert report.status == "rejected"
    assert report.reason == "result_settled"
    assert session.scalar(select(func.count()).select_from(ChaosSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 0


def test_late_scratch_voids_old_snapshot_and_appends_one_new_active_row(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    first = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT,
    )
    assert first.captured
    session.commit()

    scratched = session.get(RaceHorse, (_RACE_ID, "H12"))
    assert scratched is not None
    scratched.entry_status = EntryStatus.CANCELLED
    session.commit()

    second = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload(11, odds_offset=0.25)),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT + datetime.timedelta(minutes=1),
    )
    assert second.captured
    session.flush()

    snapshots = list(
        session.scalars(
            select(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == _RACE_ID)
            .order_by(ChaosSnapshot.captured_at)
        )
    )
    assert len(snapshots) == 2
    assert snapshots[0].status == "void"
    assert snapshots[0].void_reason == "late_scratch"
    assert snapshots[1].status == "active"
    assert snapshots[1].n == 11
    assert snapshots[0].chaos_snapshot_id != snapshots[1].chaos_snapshot_id
    assert snapshots[0].content_digest != snapshots[1].content_digest
    assert sum(snapshot.status == "active" for snapshot in snapshots) == 1
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 2


def test_same_content_recapture_keeps_digest_but_gets_a_new_snapshot_identity(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    first = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT,
    )
    assert first.captured
    session.commit()

    second = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT + datetime.timedelta(minutes=1),
    )
    assert second.captured
    session.flush()

    snapshots = list(
        session.scalars(
            select(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == _RACE_ID)
            .order_by(ChaosSnapshot.captured_at)
        )
    )
    assert snapshots[0].status == "void"
    assert snapshots[0].void_reason == "recaptured"
    assert snapshots[1].status == "active"
    assert snapshots[0].content_digest == snapshots[1].content_digest
    assert snapshots[0].chaos_snapshot_id != snapshots[1].chaos_snapshot_id


def test_partial_unique_index_remains_the_active_row_backstop(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    report = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT,
    )
    assert report.captured
    session.flush()

    session.add(
        ChaosSnapshot(
            race_id=_RACE_ID,
            captured_at=_CAPTURED_AT + datetime.timedelta(seconds=1),
            source="fixture-adapter",
            seconds_to_post=1799,
            capture_strength="confirmatory",
            field=[
                {
                    "horse_id": "H01",
                    "horse_number": 1,
                    "popularity": 1,
                    "odds": "2.5",
                }
            ],
            n=1,
            content_digest="manual-conflict",
            status="active",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
