"""Feature 043: recommend job enqueue -> worker -> terminal status + endpoint contract.

`_betting_recommend` (the betting-CLI subprocess) is monkeypatched with a fake CompletedProcess —
ops must not import the betting/model stack (boundary II/VI). Exercises run_recommend's mapping:
rc 0 + "OK:" -> succeeded, rc 0 + "SKIPPED:" -> skipped, non-zero -> failed (no worker retry).
"""

from __future__ import annotations

import subprocess

import pytest
from horseracing_db.models import IngestionJob
from sqlalchemy import select

from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_recommend
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_recommend_succeeded(session, monkeypatch):
    seed_race(session, race_id=RID)
    monkeypatch.setattr(runner_mod, "_betting_recommend",
                        lambda rid: _proc(0, "OK: run=42 recommendations=12"))
    job, reused = enqueue_recommend(session, RID)
    session.commit()
    assert reused is False
    assert drain(session) == 1
    session.refresh(job)
    assert job.status == "succeeded"
    assert job.summary["kind"] == "recommend" and job.summary["source"] == "manual"


def test_recommend_skipped_no_odds(session, monkeypatch):
    seed_race(session, race_id=RID)
    monkeypatch.setattr(runner_mod, "_betting_recommend",
                        lambda rid: _proc(0, f"SKIPPED: no win odds for race {rid} (need odds)"))
    job, _ = enqueue_recommend(session, RID)
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == "skipped"
    assert "odds" in job.summary["reason"]


def test_recommend_failed_no_retry(session, monkeypatch):
    seed_race(session, race_id=RID)
    monkeypatch.setattr(runner_mod, "_betting_recommend",
                        lambda rid: _proc(2, stderr="boom"))
    job, _ = enqueue_recommend(session, RID)
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == "failed"
    assert job.retry_count == 0  # deterministic failure, not worker-retried


def test_recommend_endpoint_202_and_audit(client, session):
    seed_race(session, race_id=RID)
    r = client.post(f"/ops/v1/races/{RID}/recommend")
    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    job = session.scalars(
        select(IngestionJob).where(IngestionJob.job_type == "recommend")
    ).first()
    assert job is not None and job.summary["kind"] == "recommend"


def test_recommend_endpoint_404_unknown_race(client, session):
    assert client.post("/ops/v1/races/202406050999/recommend").status_code == 404
