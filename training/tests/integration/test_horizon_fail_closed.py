"""Feature 086 reporting fails closed on the committed window-less artifact."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from horseracing_db.models import ChaosReadout, ChaosSnapshot, Race
from horseracing_probability.chaos_artifact import ChaosArtifactPrimaryHorizonError
from sqlalchemy import func, select

from horseracing_training import chaos_bands
from horseracing_training import cli as training_cli

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LEGACY_DIGEST = "f190e65cb9bb2d59d27982c8721f8f8e65e6c31e5b53d65d367b7ca569b72782"
_LEGACY_ARTIFACT = (
    _REPO_ROOT / "artifacts" / "chaos_bands" / f"{_LEGACY_DIGEST}.json"
)
_RACE_ID = "202607260101"
_RACE_DATE = datetime.date(2026, 7, 26)
_CAPTURED_AT = datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC)


def _seed_legacy_confirmation_observation(session) -> None:
    field = [
        {
            "horse_id": f"H{rank:02d}",
            "horse_number": rank,
            "popularity": rank,
            "odds": float(rank + 1),
        }
        for rank in range(1, 11)
    ]
    session.add(
        Race(
            race_id=_RACE_ID,
            race_date=_RACE_DATE,
            race_number=1,
            venue_code="01",
        )
    )
    # Flush the parents first: ChaosSnapshot's FK to races is not an ORM relationship, so the
    # unit of work does not know to order the INSERTs.
    session.flush()
    snapshot = ChaosSnapshot(
        chaos_snapshot_id=uuid.uuid4(),
        race_id=_RACE_ID,
        captured_at=_CAPTURED_AT,
        source="netkeiba",
        capture_trigger="legacy_unknown",
        capture_policy_version="capture_policy_v0",
        seconds_to_post=1,
        capture_strength="confirmatory",
        field=field,
        n=len(field),
        content_digest="c" * 64,
        status="active",
        created_at=_CAPTURED_AT,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        ChaosReadout(
            chaos_readout_id=uuid.uuid4(),
            chaos_snapshot_id=snapshot.chaos_snapshot_id,
            artifact_version="chaosbands-v1",
            artifact_digest=_LEGACY_DIGEST,
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
    session.commit()


def test_reporting_refuses_windowless_artifact_before_loading_observations(
    session,
    monkeypatch,
    capsys,
) -> None:
    _seed_legacy_confirmation_observation(session)
    assert session.scalar(select(func.count()).select_from(ChaosReadout)) == 1

    load_calls = 0
    load_rows = chaos_bands.load_prospective_rows

    def count_loads(*args, **kwargs):
        nonlocal load_calls
        load_calls += 1
        return load_rows(*args, **kwargs)

    monkeypatch.setattr(chaos_bands, "load_prospective_rows", count_loads)

    with pytest.raises(ChaosArtifactPrimaryHorizonError):
        training_cli._load_chaos_diagnostic_artifact(
            str(_LEGACY_ARTIFACT),
            _RACE_DATE,
        )

    result = training_cli._chaos_bands_prospective_report(
        session,
        SimpleNamespace(
            artifact=str(_LEGACY_ARTIFACT),
            bootstrap_b=5,
        ),
    )

    assert result == 2
    assert "primary_horizon is required" in capsys.readouterr().err
    assert load_calls == 0
