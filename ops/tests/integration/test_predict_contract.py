"""Feature 028 (US3): predict endpoint contract (202/404/422) + audit row in ingestion_jobs."""

from __future__ import annotations

import pytest
from horseracing_db.models import IngestionJob
from sqlalchemy import select

from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_predict_accepts_202(client, session):
    seed_race(session, race_id=RID)
    r = client.post(f"/ops/v1/races/{RID}/predict")
    assert r.status_code == 202
    body = r.json()
    assert body["scope_value"] == RID and body["scope"] == "race" and body["reused"] is False
    assert f"/ops/v1/jobs/{body['job_id']}" == body["poll_url"]
    # audit: a predict job row exists (job_type=predict, source=manual)
    job = session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "predict")
        .where(IngestionJob.scope_value == RID)
    ).first()
    assert job is not None and job.scope == "race"
    assert job.summary["kind"] == "predict" and job.summary["source"] == "manual"


def test_predict_404_unknown_race(client):
    r = client.post("/ops/v1/races/202406050999/predict")
    assert r.status_code == 404 and r.json()["code"] == "race_not_found"


def test_predict_422_bad_race_id(client):
    r = client.post("/ops/v1/races/not-a-race/predict")
    assert r.status_code == 422
