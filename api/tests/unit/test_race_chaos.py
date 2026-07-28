"""Feature 084 API readout: strict schemas, frozen-row selection, provenance, and cache."""

from __future__ import annotations

import ast
import datetime
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from horseracing_db.models import ChaosReadout, ChaosSnapshot
from horseracing_probability import chaos_distribution as distribution_module
from horseracing_probability.chaos_artifact import (
    ChaosArtifactUnavailableError,
    load_chaos_artifact,
)
from horseracing_probability.chaos_distribution import ChaosDistribution
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.dialects import postgresql

from horseracing_api import chaos
from horseracing_api.routers import predictions as predictions_router
from horseracing_api.schemas import RaceChaos, RaceChaosAvailable

_REPO = Path(__file__).resolve().parents[3]
_DIGEST = "20d1e000de200a2a1ad0687ba9456cf12121f1b575dc5d87a7d482e9f9f83680"
_ARTIFACT_PATH = _REPO / "artifacts" / "chaos_bands" / f"{_DIGEST}.json"
_TARGET_DATE = datetime.date(2026, 7, 26)


def _artifact():
    return load_chaos_artifact(
        _ARTIFACT_PATH,
        approved_digests={_DIGEST},
        target_date=_TARGET_DATE,
    )


def _field(n: int = 10) -> list[dict]:
    return [
        {
            "horse_id": f"H{number}",
            "horse_number": number,
            "popularity": number,
            "odds": float(number + 1),
        }
        for number in range(1, n + 1)
    ]


def _snapshot(
    *,
    captured_at: datetime.datetime | None = None,
    status: str = "active",
    content_digest: str = "c" * 64,
    field: list[dict] | None = None,
) -> ChaosSnapshot:
    frozen = field or _field()
    return ChaosSnapshot(
        chaos_snapshot_id=uuid.uuid4(),
        race_id="202607260101",
        captured_at=captured_at or datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
        source="netkeiba",
        capture_trigger="legacy_unknown",
        capture_policy_version="capture_policy_v0",
        seconds_to_post=1800,
        capture_strength="confirmatory",
        field=frozen,
        n=len(frozen),
        content_digest=content_digest,
        status=status,
        void_reason=None,
        created_at=datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
    )


def _readout(
    snapshot: ChaosSnapshot,
    *,
    digest: str = _DIGEST,
    p_s_ge_20: str = "0.123",
) -> ChaosReadout:
    return ChaosReadout(
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
        computed_at=datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC),
    )


def _available_payload() -> dict:
    return {
        "status": "available",
        "unavailable_reason": None,
        "band": "t3_mid",
        "band_axis": "p_s_ge_20",
        "field_size": 10,
        "feasible_support": [6, 27],
        "feasible_support_ja": "人気合計は 6〜27 の範囲",
        "events": [
            {
                "key": "s_ge_20",
                "label_ja": "人気順合計が20以上",
                "adjusted_mass": 0.12,
                "raw_mass": 0.08,
                "is_structural_zero": False,
                "structural_zero_reason": None,
                "lambda_sensitive": True,
            }
        ],
        "expected_top3_popularity_sum": 12.5,
        "within_field_size_percentile": 65.0,
        "calibration_status": "provisional",
        "calibration_basis": "closing_history_2020_2023",
        "is_market_derived": True,
        "is_pseudo": True,
        "snapshot": {
            "captured_at": "2026-07-26T04:00:00Z",
            "source": "netkeiba",
            "seconds_to_post": 1800,
            "capture_strength": "confirmatory",
            "content_digest": "c" * 64,
            "snapshot_id": str(uuid.uuid4()),
        },
        "artifact_version": "chaosbands-v1",
        "artifact_digest": _DIGEST,
        "readout_source": "persisted",
        "persisted_artifact_digest": _DIGEST,
    }


def test_race_chaos_is_a_strict_tagged_union_with_required_numbers() -> None:
    adapter = TypeAdapter(RaceChaos)
    available = adapter.validate_python(_available_payload())
    assert isinstance(available, RaceChaosAvailable)
    assert available.events[0].adjusted_mass == 0.12
    assert available.expected_top3_popularity_sum == 12.5

    unavailable = adapter.validate_python(
        {
            "status": "unavailable",
            "unavailable_reason": "no_snapshot",
            "band_axis": "p_s_ge_20",
        }
    )
    assert unavailable.status == "unavailable"

    missing_number = _available_payload()
    missing_number.pop("expected_top3_popularity_sum")
    with pytest.raises(ValidationError):
        adapter.validate_python(missing_number)

    null_nested_number = _available_payload()
    null_nested_number["events"][0]["adjusted_mass"] = None
    with pytest.raises(ValidationError):
        adapter.validate_python(null_nested_number)

    extra = _available_payload()
    extra["stale_field_name"] = 0.5
    with pytest.raises(ValidationError):
        adapter.validate_python(extra)


def test_response_models_are_constructed_without_dict_splat() -> None:
    source = Path(chaos.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    response_models = {
        "ChaosEvent",
        "ChaosSnapshotProvenance",
        "RaceChaosAvailable",
        "RaceChaosUnavailable",
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in response_models
    ]
    assert calls
    assert all(
        keyword.arg is not None
        for call in calls
        for keyword in call.keywords
    ), "response construction must use explicit keyword arguments, never Model(**dict)"


def test_router_builds_chaos_before_run_selection_and_keeps_it_in_typed_empty() -> None:
    source = Path(predictions_router.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    route = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "predictions"
    )
    calls = [
        node
        for node in ast.walk(route)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    build = next(node for node in calls if node.func.id == "build_race_chaos")
    select_run = next(node for node in calls if node.func.id == "select_prediction_run")
    assert build.lineno < select_run.lineno
    typed_empty = next(
        node
        for node in calls
        if node.func.id == "PredictionResponse"
        and any(
            keyword.arg == "run"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is None
            for keyword in node.keywords
        )
    )
    assert any(keyword.arg == "race_chaos" for keyword in typed_empty.keywords)


def test_latest_snapshot_query_filters_active_and_orders_newest_first() -> None:
    newest = _snapshot()
    scalar_result = MagicMock()
    scalar_result.first.return_value = newest
    session = MagicMock()
    session.scalars.return_value = scalar_result

    assert chaos._latest_active_snapshot(session, newest.race_id) is newest
    statement = session.scalars.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "chaos_snapshots.status = 'active'" in sql
    assert "chaos_snapshots.captured_at DESC" in sql
    assert "LIMIT 1" in sql


def test_snapshot_for_race_is_status_independent_and_limited() -> None:
    snapshot = _snapshot(status="void")
    scalar_result = MagicMock()
    scalar_result.first.return_value = snapshot
    session = MagicMock()
    session.scalars.return_value = scalar_result

    assert chaos._snapshot_for_race(session, snapshot.race_id) is snapshot
    statement = session.scalars.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "chaos_snapshots.status =" not in sql
    assert "chaos_snapshots.captured_at DESC" in sql
    assert "LIMIT 1" in sql


def test_snapshot_validation_allows_popularity_gaps_from_scratches() -> None:
    field = _field()
    for index, row in enumerate(field):
        row["popularity"] = index + 1 if index < 2 else index + 2
    frozen, reason = chaos._validate_snapshot_field(_snapshot(field=field))
    assert reason is None
    assert frozen is not None
    assert set(frozen.ranks.values()) == {1, 2, 4, 5, 6, 7, 8, 9, 10, 11}


def test_matching_digest_returns_exact_persisted_values(monkeypatch) -> None:
    artifact = _artifact()
    snapshot = _snapshot()
    readout = _readout(snapshot)
    monkeypatch.setattr(chaos, "_snapshot_for_race", lambda session, race_id: snapshot)
    monkeypatch.setattr(
        chaos,
        "_started_field_for_race",
        lambda session, race_id: snapshot.field,
    )
    monkeypatch.setattr(chaos, "_load_configured_artifact", lambda *args, **kwargs: artifact)
    monkeypatch.setattr(chaos, "_matching_readout", lambda *args, **kwargs: readout)

    result = chaos.build_race_chaos(
        MagicMock(),
        race_id=snapshot.race_id,
        target_date=_TARGET_DATE,
    )

    assert result.status == "available"
    assert result.readout_source == "persisted"
    assert result.persisted_artifact_digest == _DIGEST
    events = {event.key: event for event in result.events}
    assert events["s_ge_20"].adjusted_mass == 0.123
    assert events["s_ge_20"].raw_mass == 0.101
    assert events["himo_are"].adjusted_mass == 0.234
    assert events["total_collapse"].adjusted_mass == 0.034
    assert result.expected_top3_popularity_sum == 12.75
    assert all(event.adjusted_mass is not None for event in result.events)
    assert all(event.raw_mass is not None for event in result.events)


def test_mismatched_digest_recomputes_and_exposes_persisted_digest(monkeypatch) -> None:
    artifact = _artifact()
    snapshot = _snapshot(content_digest="d" * 64)
    old = _readout(snapshot, digest="a" * 64, p_s_ge_20="0.999")
    raw = ChaosDistribution(
        provenance="raw",
        n=10,
        support=(6, 27),
        pmf={6: 1.0},
        expected_s=11.0,
        event_mass={
            "s_ge_20": 0.11,
            "himo_are": 0.12,
            "total_collapse": 0.03,
            "s_ge_30": 0.01,
        },
        structural_zero={},
        triple_mass_sum=1.0,
    )
    adjusted = ChaosDistribution(
        provenance="stage_discount_adjusted",
        n=10,
        support=(6, 27),
        pmf={6: 1.0},
        expected_s=13.25,
        event_mass={
            "s_ge_20": 0.22,
            "himo_are": 0.23,
            "total_collapse": 0.03,
            "s_ge_30": 0.02,
        },
        structural_zero={},
        triple_mass_sum=1.0,
    )
    monkeypatch.setattr(chaos, "_snapshot_for_race", lambda session, race_id: snapshot)
    monkeypatch.setattr(
        chaos,
        "_started_field_for_race",
        lambda session, race_id: snapshot.field,
    )
    monkeypatch.setattr(chaos, "_load_configured_artifact", lambda *args, **kwargs: artifact)
    monkeypatch.setattr(chaos, "_matching_readout", lambda *args, **kwargs: None)
    monkeypatch.setattr(chaos, "_latest_readout", lambda *args, **kwargs: old)
    monkeypatch.setattr(
        chaos,
        "_derive_cached",
        lambda *args, **kwargs: (raw, adjusted, "t3_wild"),
    )

    result = chaos.build_race_chaos(
        MagicMock(),
        race_id=snapshot.race_id,
        target_date=_TARGET_DATE,
    )

    assert result.status == "available"
    assert result.readout_source == "recomputed"
    assert result.artifact_digest == _DIGEST
    assert result.persisted_artifact_digest == "a" * 64
    events = {event.key: event for event in result.events}
    assert events["s_ge_20"].adjusted_mass == 0.22
    assert events["s_ge_20"].raw_mass == 0.11
    assert result.expected_top3_popularity_sum == 13.25


def test_derivation_cache_calls_ordered_triple_engine_twice_per_key(monkeypatch) -> None:
    artifact = _artifact()
    frozen, reason = chaos._validate_snapshot_field(_snapshot(content_digest="e" * 64))
    assert frozen is not None and reason is None
    original = distribution_module.joint_probabilities
    counted = MagicMock(wraps=original)
    monkeypatch.setattr(distribution_module, "joint_probabilities", counted)
    chaos.clear_chaos_cache()

    first = chaos._derive_cached(frozen, content_digest="e" * 64, artifact=artifact)
    second = chaos._derive_cached(frozen, content_digest="e" * 64, artifact=artifact)

    assert first is second
    assert counted.call_count == 2  # raw lambda=1 plus stage-discount-adjusted


def test_artifact_configuration_has_no_default(monkeypatch) -> None:
    monkeypatch.delenv(chaos.CHAOS_ARTIFACT_PATH_ENV, raising=False)
    monkeypatch.delenv(chaos.CHAOS_APPROVED_MANIFEST_ENV, raising=False)
    with pytest.raises(ChaosArtifactUnavailableError):
        chaos._load_configured_artifact(_TARGET_DATE)


def test_configured_artifact_and_manifest_load_current_digest(monkeypatch) -> None:
    monkeypatch.setenv(chaos.CHAOS_ARTIFACT_PATH_ENV, str(_ARTIFACT_PATH))
    monkeypatch.setenv(
        chaos.CHAOS_APPROVED_MANIFEST_ENV,
        str(_REPO / "config" / "chaos_bands_approved.json"),
    )
    assert chaos._load_configured_artifact(_TARGET_DATE).artifact_digest == _DIGEST
