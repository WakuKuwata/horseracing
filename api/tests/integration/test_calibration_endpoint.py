"""T015 (US2): /models/{mv}/calibration reads walk-forward OOS reliability from metrics_summary.

Read-only (GET). OOS-only (source=walk_forward_oos). Unknown model -> 404; model present but no
reliability recorded -> typed 404 calibration_unavailable (never a silent empty curve, R8).
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion

pytestmark = pytest.mark.integration

_SUMMARY = {
    "eval": {
        "valid_years": [2008, 2009],
        "overall": {"win": {"ece": 0.012}},
        "reliability": {
            "win": {
                "n_total": 200,
                "bins": [
                    {"pred_lo": 0.0, "pred_hi": 0.1, "pred_mean": 0.05, "realized_rate": 0.06,
                     "realized_ci_low": 0.03, "realized_ci_high": 0.09, "count": 150,
                     "suppressed": False},
                    {"pred_lo": 0.5, "pred_hi": 0.6, "pred_mean": 0.55, "realized_rate": 0.5,
                     "realized_ci_low": 0.2, "realized_ci_high": 0.8, "count": 4,
                     "suppressed": True},
                ],
            }
        },
    }
}


def _seed_model(session, mv, summary):
    session.merge(ModelVersion(model_version=mv, model_family="test",
                               adoption_status=AdoptionStatus.ACTIVE, metrics_summary=summary))
    session.commit()


def test_calibration_read(client, session):
    _seed_model(session, "m-cal", _SUMMARY)
    r = client.get("/api/v1/models/m-cal/calibration")
    assert r.status_code == 200
    body = r.json()
    assert body["oos"] is True and body["source"] == "walk_forward_oos"
    assert body["valid_years"] == [2008, 2009] and body["n_total"] == 200
    assert body["ece"] == pytest.approx(0.012)
    assert len(body["bins"]) == 2
    assert body["bins"][0]["count"] == 150 and body["bins"][0]["suppressed"] is False
    assert body["bins"][1]["suppressed"] is True  # low-count bin flagged
    assert body["bins"][0]["realized_ci_low"] is not None  # Wilson CI exposed


def test_unknown_model_404(client, session):
    r = client.get("/api/v1/models/does-not-exist/calibration")
    assert r.status_code == 404
    assert r.json()["code"] == "model_not_found"


def test_model_without_reliability_404_typed(client, session):
    _seed_model(session, "m-nocal", {"eval": {"valid_years": [2008], "overall": {}}})
    r = client.get("/api/v1/models/m-nocal/calibration")
    assert r.status_code == 404
    assert r.json()["code"] == "calibration_unavailable"  # explicit, not silent empty


def test_invalid_label_422(client, session):
    _seed_model(session, "m-cal2", _SUMMARY)
    r = client.get("/api/v1/models/m-cal2/calibration", params={"label": "bogus"})
    assert r.status_code == 422
