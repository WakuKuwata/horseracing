"""Feature 051: GET /models — admin model registry. Deterministic order (active first),
metrics_summary transcription with null-safe missing keys, 200-empty on empty DB."""

from __future__ import annotations

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion

pytestmark = pytest.mark.integration

_SUMMARY = {
    "eval": {
        "overall": {"win": {"log_loss": 0.217, "auc": 0.793, "ece": 0.0006, "brier": 0.059}},
        "reliability": {"win": {"bins": [1]}},
    },
    "training": {
        "objective": "pl_topk", "calibration": "isotonic", "git_sha": "abc1234",
        "train_through": "2025-10-25", "n_model_rows": 650129,
        "adoption": {"adopted": True, "reasons": {}},
    },
    "importance": {"type": "gain", "values": {"f1": 10.0}},
}


def _mv(session, name, *, status=AdoptionStatus.CANDIDATE, summary=None):
    session.merge(ModelVersion(
        model_version=name, model_family="lightgbm", feature_version="features-012",
        adoption_status=status, metrics_summary=summary,
    ))
    session.commit()


def test_active_first_and_summary_transcribed(client, session):
    _mv(session, "lgbm-old", summary=None)                               # metrics-less candidate
    _mv(session, "lgbm-042", status=AdoptionStatus.ACTIVE, summary=_SUMMARY)

    items = client.get("/api/v1/models").json()["items"]
    assert [i["model_version"] for i in items][:1] == ["lgbm-042"]       # active first
    top = items[0]
    assert top["adoption_status"] == "active"
    assert top["win_log_loss"] == 0.217 and top["win_auc"] == 0.793
    assert top["objective"] == "pl_topk" and top["train_through"] == "2025-10-25"
    assert top["n_model_rows"] == 650129 and top["adopted"] is True
    assert top["has_calibration"] is True and top["has_importance"] is True


def test_missing_metrics_are_null_not_500(client, session):
    _mv(session, "lgbm-bare", summary=None)
    r = client.get("/api/v1/models")
    assert r.status_code == 200
    row = [i for i in r.json()["items"] if i["model_version"] == "lgbm-bare"][0]
    assert row["win_log_loss"] is None and row["train_through"] is None
    assert row["adopted"] is None
    assert row["has_calibration"] is False and row["has_importance"] is False


def test_empty_db_returns_typed_empty(client, session):
    r = client.get("/api/v1/models")
    assert r.status_code == 200 and r.json()["items"] == []
