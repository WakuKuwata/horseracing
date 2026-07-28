"""Feature 086 predict capture wiring, persistence, ordering, and retry state."""

from __future__ import annotations

import json
import subprocess

import pytest
from horseracing_db.models import IngestionJob

from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_predict
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _proc(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _capture_json(race_id: str) -> str:
    return json.dumps(
        {
            "race_id": race_id,
            "outcome": "captured",
            "reason": "ok",
            "capture_strength": "confirmatory",
            "confirmation_eligible": True,
            "seconds_to_post": 24124,
            "chaos_snapshot_id": "00000000-0000-0000-0000-000000000086",
            "elapsed_s": 0.12,
        }
    )


def _fake_capture(events: list[str], *, returncode: int = 0, stdout: str | None = None):
    def capture(race_id, *, trigger, on_launched=None):
        events.append(f"capture:{trigger}")
        if on_launched is not None:
            on_launched()
        return _proc(
            returncode,
            _capture_json(race_id) if stdout is None else stdout,
        )

    return capture


@pytest.mark.parametrize(
    ("serving", "expected_status"),
    [
        (_proc(2, stderr="serving failed"), "failed"),
        (_proc(0, stdout="no races inferred"), "skipped"),
        (_proc(0, stdout="total races persisted: 1"), "succeeded"),
    ],
)
def test_capture_fields_survive_all_three_terminal_summary_branches(
    session,
    monkeypatch,
    serving,
    expected_status,
):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    events: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_live_capture_chaos",
        _fake_capture(events),
    )
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: serving,
    )

    runner_mod.run_predict(session, job)
    session.expire_all()
    committed = session.get(IngestionJob, job.ingestion_job_id)

    assert committed is not None
    assert committed.status == expected_status
    assert committed.summary["predict_origin"] == "manual_ui"
    assert committed.summary["capture"] == {
        "state": "done",
        "outcome": "captured",
        "reason": "ok",
        "capture_strength": "confirmatory",
        "confirmation_eligible": True,
        "seconds_to_post": 24124,
        "chaos_snapshot_id": "00000000-0000-0000-0000-000000000086",
    }


@pytest.mark.parametrize("capture_failure", ["exception", "nonzero"])
def test_capture_failure_never_prevents_successful_prediction(
    session,
    monkeypatch,
    capture_failure,
):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    events: list[str] = []

    if capture_failure == "exception":
        def capture(*_args, **_kwargs):
            events.append("capture")
            raise FileNotFoundError("uv")
    else:
        capture = _fake_capture(events, returncode=2, stdout="")

    monkeypatch.setattr(runner_mod, "_live_capture_chaos", capture)

    def predict(_race_id):
        events.append("predict")
        return _proc(0, stdout="total races persisted: 1")

    monkeypatch.setattr(runner_mod, "_serving_predict", predict)

    runner_mod.run_predict(session, job)
    session.refresh(job)

    assert events[-1] == "predict"
    assert job.status == "succeeded"
    assert job.summary["capture"]["state"] == "done"
    assert job.summary["capture"]["outcome"] == "failed"
    if capture_failure == "exception":
        assert job.summary["capture"]["reason"] == "launch_failed"


def test_malformed_capture_json_is_unknown_and_prediction_continues(
    session,
    monkeypatch,
):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    monkeypatch.setattr(
        runner_mod,
        "_live_capture_chaos",
        _fake_capture([], stdout="not-json"),
    )
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: _proc(0, stdout="total races persisted: 1"),
    )

    runner_mod.run_predict(session, job)

    assert job.status == "succeeded"
    assert job.summary["capture"] == {
        "state": "done",
        "outcome": "unknown",
        "reason": "invalid_output",
    }


def test_capture_runs_immediately_before_predict(session, monkeypatch):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    events: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_live_capture_chaos",
        _fake_capture(events),
    )

    def predict(_race_id):
        events.append("predict")
        return _proc(0, stdout="no races inferred")

    monkeypatch.setattr(runner_mod, "_serving_predict", predict)

    runner_mod.run_predict(session, job)

    assert events == ["capture:predict_manual", "predict"]


@pytest.mark.parametrize("state", ["launched", "done"])
def test_launched_or_done_capture_is_never_refetched(
    session,
    monkeypatch,
    state,
):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    job.summary = {
        **job.summary,
        "capture": {"state": state, "outcome": "unknown"},
    }
    session.commit()
    calls = 0

    def capture(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("capture must not be re-fired")

    monkeypatch.setattr(runner_mod, "_live_capture_chaos", capture)
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: _proc(0, stdout="no races inferred"),
    )

    runner_mod.run_predict(session, job)

    assert calls == 0
    assert job.summary["capture"]["state"] == state


def test_started_capture_retries_once_then_never_again(session, monkeypatch):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    job.summary = {
        **job.summary,
        "capture": {
            "state": "started",
            "outcome": "unknown",
            "retried": False,
        },
    }
    session.commit()
    events: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_live_capture_chaos",
        _fake_capture(events),
    )
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: _proc(0, stdout="no races inferred"),
    )

    runner_mod.run_predict(session, job)
    runner_mod.run_predict(session, job)

    assert events == ["capture:predict_manual"]
    assert job.summary["capture"]["state"] == "done"


def test_outer_timeout_is_done_unknown_and_not_retried(session, monkeypatch):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="manual_ui")
    session.commit()
    calls = 0

    def capture(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(cmd=["uv"], timeout=18)

    monkeypatch.setattr(runner_mod, "_live_capture_chaos", capture)
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: _proc(0, stdout="no races inferred"),
    )

    runner_mod.run_predict(session, job)
    runner_mod.run_predict(session, job)

    assert calls == 1
    assert job.summary["capture"] == {
        "state": "done",
        "outcome": "unknown",
        "reason": "outer_timeout",
    }


def test_auto_capture_kill_switch_records_skip(session, monkeypatch):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="auto_after_refresh")
    session.commit()
    monkeypatch.setenv("OPS_CAPTURE_ON_AUTO_PREDICT", "false")
    monkeypatch.setattr(
        runner_mod,
        "_live_capture_chaos",
        lambda *_args, **_kwargs: pytest.fail("capture subprocess was launched"),
    )
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: _proc(0, stdout="no races inferred"),
    )

    runner_mod.run_predict(session, job)

    assert job.summary["capture"] == {
        "state": "done",
        "outcome": "skipped",
        "reason": "auto_capture_disabled",
    }


def test_auto_capture_is_enabled_by_default_and_uses_auto_trigger(
    session,
    monkeypatch,
):
    seed_race(session, race_id=RID)
    job, _ = enqueue_predict(session, RID, origin="auto_after_refresh")
    session.commit()
    monkeypatch.delenv("OPS_CAPTURE_ON_AUTO_PREDICT", raising=False)
    events: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_live_capture_chaos",
        _fake_capture(events),
    )
    monkeypatch.setattr(
        runner_mod,
        "_serving_predict",
        lambda _race_id: _proc(0, stdout="no races inferred"),
    )

    runner_mod.run_predict(session, job)

    assert events == ["capture:predict_auto"]
