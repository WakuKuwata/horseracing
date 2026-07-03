"""Feature 052: GET /coverage (per-day product coverage, active-model semantics, range guard)
and GET /jobs (ingestion_jobs history, filters, newest-first, limit cap)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import IngestionJob

from tests._synth import add_recommendation, seed_model, seed_race

pytestmark = pytest.mark.integration

_D1 = datetime.date(2008, 6, 1)
_D2 = datetime.date(2008, 6, 2)


def test_coverage_counts_and_active_semantics(client, session):
    seed_model(session)  # m-active (ACTIVE)
    # day1: race with odds + results + prediction(m-active) + recommendation
    run_id = seed_race(session, race_id="200806010101", race_date=_D1, horses={
        1: {"win": 0.4, "odds": 2.0, "finish": 1}, 2: {"win": 0.3, "odds": 3.0, "finish": 2},
    })
    add_recommendation(session, race_id="200806010101", run_id=run_id)
    # day2: race with odds only (no results / prediction by another CANDIDATE model)
    seed_model(session, model_version="m-cand", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id="200806020101", race_date=_D2, horses={
        1: {"win": 0.4, "odds": 2.0}, 2: {"win": 0.3, "odds": None},
    }, model_version="m-cand")

    body = client.get(
        f"/api/v1/coverage?date_from={_D1}&date_to={_D2}").json()
    assert body["active_model_version"] == "m-active"
    d1, d2 = body["days"]
    assert d1 == {"date": str(_D1), "n_races": 1, "n_with_odds": 1, "n_with_results": 1,
                  "n_predicted_active": 1, "n_with_recommendations": 1}
    # day2: predicted by a CANDIDATE model only → active coverage is 0 (044 semantics)
    assert d2["n_races"] == 1 and d2["n_predicted_active"] == 0
    assert d2["n_with_odds"] == 1 and d2["n_with_results"] == 0


def test_coverage_no_active_model_is_all_zero_predicted(client, session):
    seed_model(session, model_version="m-cand", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id="200806010101", race_date=_D1, horses={
        1: {"win": 0.4, "odds": 2.0},
    }, model_version="m-cand")
    body = client.get(f"/api/v1/coverage?date_from={_D1}&date_to={_D1}").json()
    assert body["active_model_version"] is None
    assert body["days"][0]["n_predicted_active"] == 0


def test_coverage_range_guards(client, session):
    r = client.get("/api/v1/coverage?date_from=2008-06-02&date_to=2008-06-01")
    assert r.status_code == 422 and r.json()["code"] == "invalid_range"
    r = client.get("/api/v1/coverage?date_from=2007-01-01&date_to=2009-01-01")
    assert r.status_code == 422 and r.json()["code"] == "range_too_wide"


def _job(session, *, job_type, status, error=None):
    j = IngestionJob(job_type=job_type, status=status, source="netkeiba",
                     error_message=error)
    session.add(j)
    session.commit()
    return j


def test_jobs_list_filters_and_order(client, session):
    _job(session, job_type="refresh_race", status="succeeded")
    _job(session, job_type="predict", status="failed", error="boom")
    _job(session, job_type="predict", status="succeeded")

    items = client.get("/api/v1/jobs").json()["items"]
    assert len(items) == 3
    # newest first (created_at DESC)
    assert items[0]["job_type"] == "predict" and items[0]["status"] == "succeeded"

    failed = client.get("/api/v1/jobs?status=failed").json()["items"]
    assert len(failed) == 1 and failed[0]["error_message"] == "boom"

    predict = client.get("/api/v1/jobs?job_type=predict").json()["items"]
    assert len(predict) == 2

    # unknown filter value → empty, not an error
    r = client.get("/api/v1/jobs?status=nonsense")
    assert r.status_code == 200 and r.json()["items"] == []

    # limit is validated (cap 200)
    assert client.get("/api/v1/jobs?limit=500").status_code == 422
    assert len(client.get("/api/v1/jobs?limit=1").json()["items"]) == 1
