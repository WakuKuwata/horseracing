"""Feature 040 T016: /models/{mv}/importance — 200 (sorted), 404 model / unavailable, read-only."""

from __future__ import annotations

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion

pytestmark = pytest.mark.integration


def _seed_model_with_importance(session, mv="m-imp", importance=None):
    session.merge(ModelVersion(
        model_version=mv, model_family="test", adoption_status=AdoptionStatus.ACTIVE,
        metrics_summary=({"importance": importance} if importance is not None else {"eval": {}}),
    ))
    session.commit()


def test_importance_200_sorted(client, session):
    _seed_model_with_importance(session, "m-imp", {
        "type": "gain",
        "values": {"te_jockey_id": 100.0, "rel_time_avg": 250.0, "venue_code": 50.0},
    })
    r = client.get("/api/v1/models/m-imp/importance")
    assert r.status_code == 200
    body = r.json()
    assert body["model_version"] == "m-imp" and body["type"] == "gain"
    feats = [v["feature"] for v in body["values"]]
    assert feats == ["rel_time_avg", "te_jockey_id", "venue_code"]  # gain desc
    assert body["values"][0]["gain"] == 250.0


def test_importance_404_model_not_found(client, session):
    r = client.get("/api/v1/models/nope/importance")
    assert r.status_code == 404
    assert r.json()["code"] == "model_not_found"


def test_importance_404_unavailable(client, session):
    _seed_model_with_importance(session, "m-noimp", importance=None)  # no importance key
    r = client.get("/api/v1/models/m-noimp/importance")
    assert r.status_code == 404
    assert r.json()["code"] == "importance_unavailable"


def test_importance_endpoint_is_get_only(client, session):
    _seed_model_with_importance(session, "m-imp2", {"type": "gain", "values": {"a": 1.0}})
    assert client.post("/api/v1/models/m-imp2/importance").status_code == 405
    assert client.delete("/api/v1/models/m-imp2/importance").status_code == 405
