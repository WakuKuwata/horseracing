"""Feature 086 capture subprocess boundary and process-group cleanup."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from horseracing_ops import runner as runner_mod


class _FakePopen:
    pid = 24680
    returncode = 0

    def __init__(self, events: list[str], *, timeout: bool = False):
        self.events = events
        self.timeout = timeout
        self.waited = False

    def communicate(self, *, timeout):
        self.events.append(f"communicate:{timeout}")
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd=["uv"], timeout=timeout)
        return ("{}", "")

    def wait(self):
        self.waited = True
        self.events.append("wait")
        return self.returncode


def test_capture_subprocess_argv_deadline_and_launch_seam(monkeypatch):
    events: list[str] = []
    seen: dict = {}
    fake = _FakePopen(events)

    def popen(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        events.append("popen")
        return fake

    monkeypatch.setattr(runner_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(
        runner_mod,
        "owner_database_url",
        lambda: "postgresql+psycopg://test/test",
    )

    result = runner_mod._live_capture_chaos(
        "202406050911",
        trigger="predict_manual",
        on_launched=lambda: events.append("launched"),
    )

    assert result.returncode == 0
    assert events == ["popen", "launched", "communicate:18"]
    assert seen["kwargs"]["start_new_session"] is True
    cmd = seen["cmd"]
    assert cmd[cmd.index("--capture-deadline-seconds") + 1] == str(
        runner_mod._CAPTURE_DEADLINE_S
    )
    assert cmd[cmd.index("--trigger") + 1] == "predict_manual"


def test_timeout_kills_and_reaps_process_group(monkeypatch):
    events: list[str] = []
    fake = _FakePopen(events, timeout=True)
    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *_a, **_kw: fake)
    monkeypatch.setattr(
        runner_mod,
        "owner_database_url",
        lambda: "postgresql+psycopg://test/test",
    )
    monkeypatch.setattr(
        runner_mod,
        "_kill_capture_process_group",
        lambda proc: events.append(f"killpg:{proc.pid}"),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        runner_mod._live_capture_chaos(
            "202406050911",
            trigger="predict_manual",
        )

    assert events == ["communicate:18", "killpg:24680"]


def test_launch_callback_failure_also_kills_process_group(monkeypatch):
    events: list[str] = []
    fake = _FakePopen(events)
    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *_a, **_kw: fake)
    monkeypatch.setattr(
        runner_mod,
        "owner_database_url",
        lambda: "postgresql+psycopg://test/test",
    )
    monkeypatch.setattr(
        runner_mod,
        "_kill_capture_process_group",
        lambda proc: events.append(f"killpg:{proc.pid}"),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        runner_mod._live_capture_chaos(
            "202406050911",
            trigger="predict_manual",
            on_launched=lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
        )

    assert events == ["killpg:24680"]


def test_process_group_kill_prevents_descendant_late_write(tmp_path):
    marker = tmp_path / "late-write"
    child_code = (
        "import pathlib,sys,time;"
        "time.sleep(0.5);"
        "pathlib.Path(sys.argv[1]).write_text('late', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
        "print('ready',flush=True);"
        "time.sleep(5)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_code, child_code, str(marker)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "ready"

    runner_mod._kill_capture_process_group(proc)
    time.sleep(0.7)

    assert not marker.exists()


def test_ops_deadline_matches_live_trigger_mapping_source():
    source = (
        Path(__file__).resolve().parents[3]
        / "live"
        / "src"
        / "horseracing_live"
        / "chaos_politeness.py"
    ).read_text(encoding="utf-8")

    assert f'"predict_manual": {runner_mod._CAPTURE_DEADLINE_S}.0' in source


def test_capture_result_projects_seven_fields_and_rejects_malformed_json():
    race_id = "202406050911"
    valid = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "race_id": race_id,
                "outcome": "captured",
                "reason": "ok",
                "capture_strength": "confirmatory",
                "confirmation_eligible": True,
                "seconds_to_post": 24124,
                "chaos_snapshot_id": "00000000-0000-0000-0000-000000000086",
                "elapsed_s": 0.1,
            }
        ),
        stderr="",
    )

    assert runner_mod._capture_result(valid, race_id=race_id) == {
        "state": "done",
        "outcome": "captured",
        "reason": "ok",
        "capture_strength": "confirmatory",
        "confirmation_eligible": True,
        "seconds_to_post": 24124,
        "chaos_snapshot_id": "00000000-0000-0000-0000-000000000086",
    }

    malformed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="not-json",
        stderr="",
    )
    assert runner_mod._capture_result(malformed, race_id=race_id) == {
        "state": "done",
        "outcome": "unknown",
        "reason": "invalid_output",
    }
