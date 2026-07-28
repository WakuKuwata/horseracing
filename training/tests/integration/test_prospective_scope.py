"""Feature 086 prospective reporting is structurally scoped to one digest."""

from __future__ import annotations

import datetime
import uuid
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

from horseracing_training.chaos_bands import load_prospective_rows

pytestmark = pytest.mark.integration

_CURRENT_DIGEST = "20d1e000de200a2a1ad0687ba9456cf12121f1b575dc5d87a7d482e9f9f83680"
_WINDOWLESS_DIGEST = "f190e65cb9bb2d59d27982c8721f8f8e65e6c31e5b53d65d367b7ca569b72782"
_RACE_DATE = datetime.date(2026, 7, 26)
_CAPTURED_AT = datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC)


def _seed_readout(session, *, race_id: str, digest: str) -> tuple[uuid.UUID, uuid.UUID]:
    field = [
        {
            "horse_id": f"{race_id}-H{rank:02d}",
            "horse_number": rank,
            "popularity": rank,
            "odds": float(rank + 1),
        }
        for rank in range(1, 11)
    ]
    snapshot_id = uuid.uuid4()
    readout_id = uuid.uuid4()
    session.add(
        Race(
            race_id=race_id,
            race_date=_RACE_DATE,
            race_number=int(race_id[-2:]),
            venue_code="01",
        )
    )
    for rank in range(1, 11):
        horse_id = f"{race_id}-H{rank:02d}"
        session.add(Horse(horse_id=horse_id, horse_name=horse_id))
        session.add(
            RaceHorse(
                race_id=race_id,
                horse_id=horse_id,
                horse_number=rank,
                entry_status=EntryStatus.STARTED,
            )
        )
    # Flush the parents first: ChaosSnapshot's FK to races is not declared as an ORM
    # relationship, so the unit of work does not know to order the INSERTs.
    session.flush()
    snapshot = ChaosSnapshot(
        chaos_snapshot_id=snapshot_id,
        race_id=race_id,
        captured_at=_CAPTURED_AT,
        source="netkeiba",
        capture_trigger="predict_manual",
        capture_policy_version="capture_policy_v1",
        seconds_to_post=1_800,
        capture_strength="confirmatory",
        field=field,
        n=len(field),
        content_digest=race_id.ljust(64, "0"),
        status="active",
        created_at=_CAPTURED_AT,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        ChaosReadout(
            chaos_readout_id=readout_id,
            chaos_snapshot_id=snapshot_id,
            artifact_version="chaosbands-v1",
            artifact_digest=digest,
            band="t3_rough",
            band_axis="p_s_ge_20",
            p_s_ge_20=Decimal("0.20"),
            p_himo_are=Decimal("0.10"),
            p_total_collapse=Decimal("0.05"),
            raw_p_s_ge_20=Decimal("0.18"),
            raw_p_himo_are=Decimal("0.08"),
            raw_p_total_collapse=Decimal("0.05"),
            expected_s=Decimal("12.5"),
            structural_zeros={},
            computed_at=_CAPTURED_AT,
        )
    )
    return snapshot_id, readout_id


def test_prospective_rows_cannot_cross_artifact_digest_scope(session) -> None:
    current_snapshot, current_readout = _seed_readout(
        session,
        race_id="202607260101",
        digest=_CURRENT_DIGEST,
    )
    legacy_snapshot, legacy_readout = _seed_readout(
        session,
        race_id="202607260102",
        digest=_WINDOWLESS_DIGEST,
    )
    session.commit()

    current_rows = load_prospective_rows(
        session,
        artifact_digest=_CURRENT_DIGEST,
        through_date=_RACE_DATE,
    )
    legacy_rows = load_prospective_rows(
        session,
        artifact_digest=_WINDOWLESS_DIGEST,
        through_date=_RACE_DATE,
    )

    assert [
        (
            row.race_id,
            row.snapshot_id,
            row.readout_id,
            row.capture_trigger,
            row.current_started_field,
        )
        for row in current_rows
    ] == [
        (
            "202607260101",
            str(current_snapshot),
            str(current_readout),
            "predict_manual",
            frozenset(
                (f"202607260101-H{rank:02d}", rank)
                for rank in range(1, 11)
            ),
        )
    ]
    assert [
        (row.race_id, row.snapshot_id, row.readout_id) for row in legacy_rows
    ] == [
        ("202607260102", str(legacy_snapshot), str(legacy_readout))
    ]
