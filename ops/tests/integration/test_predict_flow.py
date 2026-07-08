"""Feature 028 (US1/US2): predict job enqueue -> worker -> terminal status.

`_serving_predict` (the serving-CLI subprocess) is monkeypatched with a fake CompletedProcess — ops
must not import the model stack (boundary II/VI), and tests stay network/model-free. We exercise
run_predict's mapping: rc 0 + output -> succeeded, rc 0 + "no races inferred" -> skipped,
non-zero -> failed (no worker retry)."""

from __future__ import annotations

import subprocess

import pytest
from horseracing_db.models import IngestionJob

from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_predict, enqueue_recommend
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_predict_succeeded_chains_auto_recommend(session, monkeypatch, client):
    seed_race(session, race_id=RID)
    out = f"model_version=lgbm-026 logic_version=x\n  race={RID} run=42 horses=3\ntotal races persisted: 1"
    monkeypatch.setattr(runner_mod, "_serving_predict", lambda rid: _proc(0, out))
    monkeypatch.setattr(runner_mod, "_betting_recommend",
                        lambda rid: _proc(0, "OK: run=42 win=4 exotic=29"))
    job, reused = enqueue_predict(session, RID)
    session.commit()
    assert reused is False
    # predict + the follow-up recommend it enqueues (043 deferred: auto-generate on predict)
    assert drain(session) == 2
    session.refresh(job)
    assert job.status == "succeeded"
    assert job.summary["kind"] == "predict" and job.summary["source"] == "manual"

    followup = session.get(IngestionJob, job.summary["recommend_job_id"])
    assert followup is not None and followup.job_type == "recommend"
    assert followup.status == "succeeded"
    assert followup.summary["source"] == "auto_after_predict"  # survives run_recommend's rewrite
    # the poll endpoint exposes the chain so the front can follow it
    body = client.get(f"/ops/v1/jobs/{job.ingestion_job_id}").json()
    assert body["followup_job_id"] == str(followup.ingestion_job_id)


def test_predict_reuses_inflight_recommend_no_duplicate(session, monkeypatch):
    # a manual 買い目生成 is still queued when the predict finishes → the follow-up reuses it
    # (in-flight dedup), so the two paths converge on ONE recommend job.
    seed_race(session, race_id=RID)
    out = f"race={RID} run=42 horses=3\ntotal races persisted: 1"
    monkeypatch.setattr(runner_mod, "_serving_predict", lambda rid: _proc(0, out))
    predict_job, _ = enqueue_predict(session, RID)
    session.commit()
    manual, _ = enqueue_recommend(session, RID)  # user clicks 買い目生成 while predict is queued
    session.commit()

    monkeypatch.setattr(runner_mod, "_betting_recommend",
                        lambda rid: _proc(0, "OK: run=42 win=1 exotic=2"))
    assert drain(session) == 2  # predict (whose follow-up reuses `manual`) + the manual job itself
    session.refresh(predict_job)
    assert predict_job.summary["recommend_job_id"] == str(manual.ingestion_job_id)
    n_recommend = session.query(IngestionJob).filter_by(job_type="recommend").count()
    assert n_recommend == 1


def test_predict_does_not_reuse_running_recommend(session, monkeypatch):
    # a RUNNING recommend (concurrent worker thread) may have resolved the PRE-predict run —
    # the follow-up must NOT adopt it as "the fresh run's buy-ups"; it enqueues its own job.
    seed_race(session, race_id=RID)
    out = f"race={RID} run=42 horses=3\ntotal races persisted: 1"
    monkeypatch.setattr(runner_mod, "_serving_predict", lambda rid: _proc(0, out))
    monkeypatch.setattr(runner_mod, "_betting_recommend",
                        lambda rid: _proc(0, "OK: run=42 win=1 exotic=2"))
    inflight, _ = enqueue_recommend(session, RID)
    inflight.status = "running"  # simulate: claimed by another worker thread mid-generation
    session.commit()

    predict_job, _ = enqueue_predict(session, RID)
    session.commit()
    drain(session)  # claims predict (running recommend is not claimable) + the new follow-up
    session.refresh(predict_job)
    followup_id = predict_job.summary["recommend_job_id"]
    assert followup_id != str(inflight.ingestion_job_id)
    assert session.query(IngestionJob).filter_by(job_type="recommend").count() == 2


def test_predict_skipped_when_no_started_horses(session, monkeypatch):
    seed_race(session, race_id=RID)
    monkeypatch.setattr(runner_mod, "_serving_predict",
                        lambda rid: _proc(0, "no races inferred (no started horses / out of scope)"))
    job, _ = enqueue_predict(session, RID)
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == "skipped"
    assert job.summary["reason"] == "no started horses"  # no half-baked prediction_run
    # no run was created → no auto-recommend follow-up
    assert session.query(IngestionJob).filter_by(job_type="recommend").count() == 0


def test_predict_failed_on_serving_error_no_retry(session, monkeypatch):
    seed_race(session, race_id=RID)
    monkeypatch.setattr(
        runner_mod, "_serving_predict",
        lambda rid: _proc(2, stderr="predict: error: multiple active models (lgbm-026, lgbm-027)"),
    )
    job, _ = enqueue_predict(session, RID)
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == "failed"
    assert "multiple active" in (job.summary.get("error") or "")
    assert job.retry_count == 0  # deterministic failure mapped in run_predict, not worker-retried
    assert session.query(IngestionJob).filter_by(job_type="recommend").count() == 0
