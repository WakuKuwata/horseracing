"""B9 (2026-10-28): GET /api/v1/races/{race_id}/chaos — the 084 readout on its own path.

The object must be byte-identical to ``race_chaos`` of ``/predictions`` for the same race (same
builder, same arguments), available without any prediction run, typed on every failure state, and
the usual 422 / 404 for a bad or unknown race id.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_api import chaos
from tests.integration.test_race_chaos_api import (
    _ARTIFACT,
    _DIGEST,
    _MANIFEST,
    _RACE,
    _seed_race_without_run,
    _seed_readout,
    _seed_snapshot,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _configure_artifact(monkeypatch):
    monkeypatch.setenv(chaos.CHAOS_ARTIFACT_PATH_ENV, str(_ARTIFACT))
    monkeypatch.setenv(chaos.CHAOS_APPROVED_MANIFEST_ENV, str(_MANIFEST))
    chaos.clear_chaos_cache()
    yield
    chaos.clear_chaos_cache()


def _url(race_id: str = _RACE) -> str:
    return f"/api/v1/races/{race_id}/chaos"


def test_invalid_and_unknown_race_ids_are_typed(client):
    bad = client.get(_url("nope"))
    assert bad.status_code == 422
    assert bad.json() == {"status": 422, "code": "invalid_race_id",
                          "detail": "race_id must be 12 digits"}
    missing = client.get(_url("202607269999"))
    assert missing.status_code == 404
    assert missing.json()["code"] == "race_not_found"


def test_available_readout_equals_the_predictions_object_without_a_run(client, session):
    _seed_race_without_run(session)
    active = _seed_snapshot(
        session, captured_at=datetime.datetime(2026, 7, 26, 4, 0, tzinfo=datetime.UTC)
    )
    _seed_readout(session, active, p_s_ge_20="0.123")

    resp = client.get(_url())
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"race_id", "race_chaos"}
    assert body["race_id"] == _RACE
    readout = body["race_chaos"]
    assert readout["status"] == "available"
    assert readout["artifact_digest"] == _DIGEST
    assert readout["snapshot"]["snapshot_id"] == str(active.chaos_snapshot_id)
    # the same object the legacy response carries (same builder, same arguments)
    legacy = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    assert legacy["run"] is None
    assert readout == legacy["race_chaos"]


def test_no_snapshot_is_a_typed_200(client, session):
    _seed_race_without_run(session)
    resp = client.get(_url())
    assert resp.status_code == 200
    assert resp.json() == {
        "race_id": _RACE,
        "race_chaos": {"status": "unavailable", "unavailable_reason": "no_snapshot",
                       "band_axis": "p_s_ge_20"},
    }


def test_missing_artifact_configuration_is_unavailable_not_500(client, session, monkeypatch):
    _seed_race_without_run(session)
    active = _seed_snapshot(session)
    _seed_readout(session, active)
    monkeypatch.delenv(chaos.CHAOS_ARTIFACT_PATH_ENV, raising=False)
    chaos.clear_chaos_cache()
    resp = client.get(_url())
    assert resp.status_code == 200
    assert resp.json()["race_chaos"]["status"] == "unavailable"
    assert resp.json()["race_chaos"]["unavailable_reason"] == "artifact_unavailable"
