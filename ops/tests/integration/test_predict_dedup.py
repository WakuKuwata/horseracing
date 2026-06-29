"""Feature 028: predict dedup — in-flight reuse, completed not reused, no clash with refresh."""

from __future__ import annotations

import pytest

from horseracing_ops.enqueue import enqueue_predict, enqueue_race
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_inflight_predict_is_reused(session):
    seed_race(session, race_id=RID)
    j1, r1 = enqueue_predict(session, RID)
    j2, r2 = enqueue_predict(session, RID)
    session.commit()
    assert r1 is False and r2 is True
    assert j1.ingestion_job_id == j2.ingestion_job_id  # double-click → single job


def test_completed_predict_is_not_reused(session):
    seed_race(session, race_id=RID)
    j1, _ = enqueue_predict(session, RID)
    j1.status = "succeeded"  # finished → an explicit click should (re)generate
    session.commit()
    j2, reused = enqueue_predict(session, RID)
    session.commit()
    assert reused is False and j2.ingestion_job_id != j1.ingestion_job_id


def test_predict_and_refresh_do_not_clash(session):
    seed_race(session, race_id=RID)
    jp, _ = enqueue_predict(session, RID)
    jr, _ = enqueue_race(session, RID)  # different job_type / advisory key → independent
    session.commit()
    assert jp.job_type == "predict" and jr.job_type == "refresh_race"
    assert jp.ingestion_job_id != jr.ingestion_job_id
