"""Feature 084 predictions integration: run-independent chaos and fail-closed states."""

from __future__ import annotations

import datetime
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from horseracing_db.models import ChaosReadout, ChaosSnapshot, Race
from horseracing_probability.chaos_distribution import ChaosInvariantError

from horseracing_api import chaos
from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_DIGEST = "f190e65cb9bb2d59d27982c8721f8f8e65e6c31e5b53d65d367b7ca569b72782"
_ARTIFACT = _REPO / "artifacts" / "chaos_bands" / f"{_DIGEST}.json"
_MANIFEST = _REPO / "config" / "chaos_bands_approved.json"
_RACE = "202607260101"


@pytest.fixture(autouse=True)
def _configure_artifact(monkeypatch):
    monkeypatch.setenv(chaos.CHAOS_ARTIFACT_PATH_ENV, str(_ARTIFACT))
    monkeypatch.setenv(chaos.CHAOS_APPROVED_MANIFEST_ENV, str(_MANIFEST))
    chaos.clear_chaos_cache()
    yield
    chaos.clear_chaos_cache()


def _field(n: int = 10) -> list[dict]:
    return [
        {
            "horse_id": f"F{number}",
            "horse_number": number,
            "popularity": number,
            "odds": float(number + 1),
        }
        for number in range(1, n + 1)
    ]


def _seed_race_without_run(
    session,
    *,
    race_id: str = _RACE,
    race_date: datetime.date = datetime.date(2026, 7, 26),
) -> None:
    session.add(
        Race(
            race_id=race_id,
            race_date=race_date,
            race_number=1,
            venue_code="01",
        )
    )
    session.commit()


def _seed_snapshot(
    session,
    *,
    race_id: str = _RACE,
    field: list[dict] | None = None,
    status: str = "active",
    captured_at: datetime.datetime | None = None,
    content_digest: str = "c" * 64,
) -> ChaosSnapshot:
    frozen = field if field is not None else _field()
    snapshot = ChaosSnapshot(
        chaos_snapshot_id=uuid.uuid4(),
        race_id=race_id,
        captured_at=captured_at
        or datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
        source="netkeiba",
        seconds_to_post=1800,
        capture_strength="confirmatory",
        field=frozen,
        n=len(frozen),
        content_digest=content_digest,
        status=status,
        void_reason=("recaptured" if status == "void" else None),
        created_at=datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def _seed_readout(
    session,
    snapshot: ChaosSnapshot,
    *,
    digest: str = _DIGEST,
    p_s_ge_20: str = "0.123",
) -> ChaosReadout:
    readout = ChaosReadout(
        chaos_readout_id=uuid.uuid4(),
        chaos_snapshot_id=snapshot.chaos_snapshot_id,
        artifact_version="chaosbands-v1",
        artifact_digest=digest,
        band="t3_rough",
        band_axis="p_s_ge_20",
        p_s_ge_20=Decimal(p_s_ge_20),
        p_himo_are=Decimal("0.234"),
        p_total_collapse=Decimal("0.034"),
        raw_p_s_ge_20=Decimal("0.101"),
        raw_p_himo_are=Decimal("0.202"),
        raw_p_total_collapse=Decimal("0.034"),
        expected_s=Decimal("12.75"),
        structural_zeros={},
        computed_at=snapshot.captured_at,
    )
    session.add(readout)
    session.commit()
    return readout


def test_no_prediction_run_still_returns_available_chaos_from_latest_active(
    client,
    session,
) -> None:
    _seed_race_without_run(session)
    active = _seed_snapshot(
        session,
        captured_at=datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
    )
    _seed_readout(session, active, p_s_ge_20="0.123")
    # A newer void row must never replace the active display default (SNAP-6).
    void = _seed_snapshot(
        session,
        status="void",
        captured_at=datetime.datetime(2026, 7, 26, 4, 5, tzinfo=datetime.UTC),
        content_digest="v" * 64,
    )
    _seed_readout(session, void, p_s_ge_20="0.999")

    response = client.get(f"/api/v1/races/{_RACE}/predictions")

    assert response.status_code == 200
    body = response.json()
    assert body["run"] is None
    assert body["horses"] == []
    readout = body["race_chaos"]
    assert readout["status"] == "available"
    assert readout["snapshot"]["snapshot_id"] == str(active.chaos_snapshot_id)
    assert readout["readout_source"] == "persisted"
    assert readout["artifact_digest"] == _DIGEST
    assert readout["persisted_artifact_digest"] == _DIGEST
    assert readout["field_size"] == 10
    assert readout["feasible_support"] == [6, 27]
    assert readout["expected_top3_popularity_sum"] == 12.75
    events = {event["key"]: event for event in readout["events"]}
    assert events["s_ge_20"]["adjusted_mass"] == 0.123
    assert events["s_ge_20"]["raw_mass"] == 0.101
    assert events["himo_are"]["adjusted_mass"] == 0.234
    assert events["total_collapse"]["adjusted_mass"] == 0.034
    assert all(event["adjusted_mass"] is not None for event in readout["events"])
    assert all(event["raw_mass"] is not None for event in readout["events"])


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("no_snapshot", "no_snapshot"),
        ("partial_market_odds", "partial_market_odds"),
        ("invalid_popularity_ranks", "invalid_popularity_ranks"),
        ("field_too_small", "field_too_small"),
        ("artifact_unavailable", "artifact_unavailable"),
        ("out_of_validity_window", "out_of_validity_window"),
        ("invariant_violation", "invariant_violation"),
    ],
)
def test_unavailable_reasons_remain_http_200(
    client,
    session,
    monkeypatch,
    case: str,
    expected_reason: str,
) -> None:
    target_date = (
        datetime.date(2023, 12, 31)
        if case == "out_of_validity_window"
        else datetime.date(2026, 7, 26)
    )
    _seed_race_without_run(session, race_date=target_date)

    if case != "no_snapshot":
        field = _field(3 if case == "field_too_small" else 10)
        if case == "partial_market_odds":
            field[0]["odds"] = None
        if case == "invalid_popularity_ranks":
            field[1]["popularity"] = field[0]["popularity"]
        _seed_snapshot(
            session,
            field=field,
            content_digest=case.ljust(64, "x")[:64],
        )

    if case == "artifact_unavailable":
        monkeypatch.setenv(
            chaos.CHAOS_ARTIFACT_PATH_ENV,
            str(_REPO / "artifacts" / "chaos_bands" / "missing.json"),
        )
    if case == "invariant_violation":
        def fail_invariant(*args, **kwargs):
            raise ChaosInvariantError("forced integration invariant failure")

        monkeypatch.setattr(chaos, "chaos_readout", fail_invariant)

    response = client.get(f"/api/v1/races/{_RACE}/predictions")

    assert response.status_code == 200
    assert response.json()["race_chaos"] == {
        "status": "unavailable",
        "unavailable_reason": expected_reason,
        "band_axis": "p_s_ge_20",
    }


def test_artifact_mismatch_recomputes_and_reports_recorded_digest(
    client,
    session,
) -> None:
    _seed_race_without_run(session)
    snapshot = _seed_snapshot(session, content_digest="m" * 64)
    _seed_readout(session, snapshot, digest="a" * 64, p_s_ge_20="0.999")

    response = client.get(f"/api/v1/races/{_RACE}/predictions")

    assert response.status_code == 200
    readout = response.json()["race_chaos"]
    assert readout["status"] == "available"
    assert readout["readout_source"] == "recomputed"
    assert readout["artifact_digest"] == _DIGEST
    assert readout["persisted_artifact_digest"] == "a" * 64
    assert readout["events"][0]["adjusted_mass"] != 0.999


def test_066_race_dispersion_is_byte_identical_when_chaos_appears(
    client,
    session,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DISPERSION_BOUNDARY_PATH", raising=False)
    monkeypatch.delenv("DISPERSION_CALIB_MANIFEST", raising=False)
    monkeypatch.delenv("DISPERSION_PCAL_PATH", raising=False)
    seed_model(session)
    seed_race(
        session,
        race_id=_RACE,
        race_date=datetime.date(2026, 7, 26),
        horses={
            1: {"win": 0.45, "odds": 2.0},
            2: {"win": 0.25, "odds": 3.5},
            3: {"win": 0.18, "odds": 6.0},
            4: {"win": 0.12, "odds": 9.0},
        },
    )

    before = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    snapshot = _seed_snapshot(session)
    _seed_readout(session, snapshot)
    after = client.get(f"/api/v1/races/{_RACE}/predictions").json()

    assert before["race_chaos"]["status"] == "unavailable"
    assert after["race_chaos"]["status"] == "available"
    encoded_before = json.dumps(
        before["race_dispersion"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    encoded_after = json.dumps(
        after["race_dispersion"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert encoded_after == encoded_before


def test_runtime_openapi_matches_both_committed_snapshots() -> None:
    from horseracing_api.app import app

    front_path = _REPO / "front" / "openapi.json"
    admin_path = _REPO / "admin" / "openapi.json"
    assert front_path.read_bytes() == admin_path.read_bytes()
    expected = json.loads(front_path.read_text(encoding="utf-8"))
    assert app.openapi() == expected
