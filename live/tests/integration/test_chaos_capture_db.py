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
_CAPTURE_TRIGGER = "explicit_command"
_CAPTURE_POLICY_VERSION = "capture_policy_v1"


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
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
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
    assert snapshot.capture_trigger == _CAPTURE_TRIGGER
    assert snapshot.capture_policy_version == _CAPTURE_POLICY_VERSION
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


def test_result_pending_is_reverified_after_fetch_before_either_write(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    states = iter((True, False))
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
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT,
        pending_check=pending_check,
    )

    assert checks == 2
    assert report.status == "skipped"
    assert report.reason == "result_settled_during_fetch"
    assert session.scalar(select(func.count()).select_from(ChaosSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 0


def test_field_change_voids_snapshot_in_place_without_growing_rows(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    first = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT,
    )
    assert first.captured
    session.commit()

    scratched = session.get(RaceHorse, (_RACE_ID, "H12"))
    assert scratched is not None
    scratched.entry_status = EntryStatus.CANCELLED
    session.commit()

    second_fetcher = StubFetcher(_payload(11, odds_offset=0.25))
    second = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=second_fetcher,
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT + datetime.timedelta(minutes=1),
    )
    assert second.status == "skipped"
    assert second.reason == "already_captured"
    assert second_fetcher.calls == []
    session.flush()

    snapshots = list(
        session.scalars(
            select(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == _RACE_ID)
            .order_by(ChaosSnapshot.captured_at)
        )
    )
    assert len(snapshots) == 1
    assert snapshots[0].status == "void"
    assert snapshots[0].void_reason == "field_changed"
    assert snapshots[0].n == 12
    assert snapshots[0].chaos_snapshot_id == first.chaos_snapshot_id
    assert snapshots[0].content_digest == first.content_digest
    assert sum(snapshot.status == "active" for snapshot in snapshots) == 0
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1


def test_horse_number_change_voids_snapshot_in_place_without_recapture(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    first = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT,
    )
    assert first.captured
    session.commit()

    renumbered = session.get(RaceHorse, (_RACE_ID, "H12"))
    assert renumbered is not None
    renumbered.horse_number = 13
    session.commit()

    second_fetcher = StubFetcher(_payload())
    second = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=second_fetcher,
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT + datetime.timedelta(minutes=1),
    )
    assert second.status == "skipped"
    assert second.reason == "already_captured"
    assert second_fetcher.calls == []
    session.flush()

    snapshots = list(
        session.scalars(
            select(ChaosSnapshot)
            .where(ChaosSnapshot.race_id == _RACE_ID)
            .order_by(ChaosSnapshot.captured_at)
        )
    )
    assert len(snapshots) == 1
    assert snapshots[0].status == "void"
    assert snapshots[0].void_reason == "field_changed"
    assert snapshots[0].content_digest == first.content_digest
    assert snapshots[0].chaos_snapshot_id == first.chaos_snapshot_id
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1


def test_same_field_rerun_keeps_active_snapshot_without_fetch_or_write(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    first = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT,
    )
    assert first.captured
    session.commit()

    second_fetcher = StubFetcher(_payload(odds_offset=10.0))
    second = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=second_fetcher,
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT + datetime.timedelta(minutes=1),
    )

    assert second.status == "skipped"
    assert second.reason == "already_captured"
    assert second_fetcher.calls == []
    snapshots = list(
        session.scalars(select(ChaosSnapshot).where(ChaosSnapshot.race_id == _RACE_ID))
    )
    assert len(snapshots) == 1
    assert snapshots[0].status == "active"
    assert snapshots[0].void_reason is None
    assert snapshots[0].chaos_snapshot_id == first.chaos_snapshot_id
    assert snapshots[0].content_digest == first.content_digest
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1


def test_partial_unique_index_remains_the_active_row_backstop(session):
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    report = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(_payload()),
        artifact=artifact,
        capture_trigger=_CAPTURE_TRIGGER,
        capture_policy_version=_CAPTURE_POLICY_VERSION,
        deadline=float("inf"),
        clock=lambda: _CAPTURED_AT,
    )
    assert report.captured
    session.flush()

    session.add(
        ChaosSnapshot(
            race_id=_RACE_ID,
            captured_at=_CAPTURED_AT + datetime.timedelta(seconds=1),
            source="fixture-adapter",
            capture_trigger=_CAPTURE_TRIGGER,
            capture_policy_version=_CAPTURE_POLICY_VERSION,
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
