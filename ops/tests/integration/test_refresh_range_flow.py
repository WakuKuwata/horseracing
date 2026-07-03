"""Feature 053: refresh-range job enqueue -> worker -> terminal status.

`_live_refresh` (the live-CLI subprocess) is monkeypatched — ops must not import the
live/serving/betting stack (boundary II/VI), and tests stay model-free. run_refresh_range maps
rc 0 -> succeeded, non-zero -> failed (no worker retry; the 050 pipeline is idempotent)."""

from __future__ import annotations

import datetime
import subprocess

import pytest

from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_refresh_range
from horseracing_ops.worker import drain

pytestmark = pytest.mark.integration

_FROM = datetime.date(2025, 1, 5)
_TO = datetime.date(2025, 1, 6)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_refresh_range_succeeded(session, monkeypatch):
    out = ("refresh 2025-01-05..2025-01-06\n"
           "  predict:   generated=0 skip_exists=48 skip_no_started=0 error_days=0\n"
           "  recommend: races=48 generated=0 topped_up=0 skip_exists=48 ...")
    captured = {}

    def fake(date_from, date_to):
        captured["args"] = (date_from, date_to)
        return _proc(0, out)

    monkeypatch.setattr(runner_mod, "_live_refresh", fake)
    job, reused = enqueue_refresh_range(session, _FROM, _TO)
    session.commit()
    assert reused is False
    assert drain(session) == 1
    session.refresh(job)
    assert job.status == "succeeded"
    assert job.summary["kind"] == "refresh_range"
    # the runner parses "from..to" out of scope_value and passes the two ISO dates through
    assert captured["args"] == ("2025-01-05", "2025-01-06")


def test_refresh_range_failed_no_retry(session, monkeypatch):
    monkeypatch.setattr(
        runner_mod, "_live_refresh",
        lambda df, dt: _proc(1, stderr="refresh: predict FAILED — model artifact missing"),
    )
    job, _ = enqueue_refresh_range(session, _FROM, _TO)
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == "failed"
    assert "artifact missing" in (job.summary.get("error") or "")
    assert job.retry_count == 0  # deterministic failure mapped in the runner, not worker-retried
