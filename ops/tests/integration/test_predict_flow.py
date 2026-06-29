"""Feature 028 (US1/US2): predict job enqueue -> worker -> terminal status.

`_serving_predict` (the serving-CLI subprocess) is monkeypatched with a fake CompletedProcess — ops
must not import the model stack (boundary II/VI), and tests stay network/model-free. We exercise
run_predict's mapping: rc 0 + output -> succeeded, rc 0 + "no races inferred" -> skipped,
non-zero -> failed (no worker retry)."""

from __future__ import annotations

import subprocess

import pytest

from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_predict
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_predict_succeeded(session, monkeypatch):
    seed_race(session, race_id=RID)
    out = f"model_version=lgbm-026 logic_version=x\n  race={RID} run=42 horses=3\ntotal races persisted: 1"
    monkeypatch.setattr(runner_mod, "_serving_predict", lambda rid: _proc(0, out))
    job, reused = enqueue_predict(session, RID)
    session.commit()
    assert reused is False
    assert drain(session) == 1
    session.refresh(job)
    assert job.status == "succeeded"
    assert job.summary["kind"] == "predict" and job.summary["source"] == "manual"


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
