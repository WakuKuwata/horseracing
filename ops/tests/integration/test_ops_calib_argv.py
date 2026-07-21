"""Feature 076 US2 (T018): ops forwards manifest activation to the live subprocess (argv only).

ops must NOT import the ML stack — it only shells out to `live refresh`. When a manifest is
configured (env, opt-in), the calib flags are appended to the subprocess argv so the SAME live-CLI
loader resolves the SAME manifest_digest as a direct CLI run (SC-011). Default (no env) argv is
unchanged (backward-compatible).
"""

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
_MANIFEST = "/abs/path/to/manifest.json"


def _proc(rc: int = 0, stdout: str = "ok", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_default_argv_has_no_calib_flags(session, monkeypatch):
    monkeypatch.delenv("REFRESH_CALIB_MANIFEST", raising=False)
    monkeypatch.delenv("REFRESH_CALIB_MODE", raising=False)
    seen = {}

    def fake(date_from, date_to):  # legacy 2-arg signature stays valid (backward-compat)
        seen["args"] = (date_from, date_to)
        return _proc()

    monkeypatch.setattr(runner_mod, "_live_refresh", fake)
    job, _ = enqueue_refresh_range(session, _FROM, _TO)
    session.commit()
    assert drain(session) == 1
    session.refresh(job)
    assert job.status == "succeeded"
    assert seen["args"] == ("2025-01-05", "2025-01-06")


def test_manifest_env_forwards_calib_flags(session, monkeypatch):
    monkeypatch.setenv("REFRESH_CALIB_MANIFEST", _MANIFEST)
    monkeypatch.setenv("REFRESH_CALIB_MODE", "manifest-required")
    seen = {}

    def fake(date_from, date_to, *, calib_manifest=None, calib_mode="legacy-runtime"):
        seen.update(manifest=calib_manifest, mode=calib_mode)
        return _proc()

    monkeypatch.setattr(runner_mod, "_live_refresh", fake)
    job, _ = enqueue_refresh_range(session, _FROM, _TO)
    session.commit()
    assert drain(session) == 1
    session.refresh(job)
    assert job.status == "succeeded"
    assert seen == {"manifest": _MANIFEST, "mode": "manifest-required"}


def test_live_refresh_builds_calib_argv(monkeypatch):
    """The real _live_refresh appends the flags to the subprocess argv (captured, not run)."""
    import horseracing_ops.runner as rm
    captured = {}
    monkeypatch.setattr(rm, "owner_database_url", lambda: "postgresql+psycopg://x/y")
    monkeypatch.setattr(rm.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd) or _proc())
    rm._live_refresh("2025-01-05", "2025-01-06",
                     calib_manifest=_MANIFEST, calib_mode="manifest-required")
    cmd = captured["cmd"]
    assert "--calib-manifest" in cmd and _MANIFEST in cmd
    assert cmd[cmd.index("--calib-mode") + 1] == "manifest-required"


def test_live_refresh_default_argv_has_no_calib(monkeypatch):
    import horseracing_ops.runner as rm
    captured = {}
    monkeypatch.setattr(rm, "owner_database_url", lambda: "postgresql+psycopg://x/y")
    monkeypatch.setattr(rm.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd) or _proc())
    rm._live_refresh("2025-01-05", "2025-01-06")
    assert "--calib-manifest" not in captured["cmd"]


# --- 076-gap (codex): per-race predict/recommend subprocess argv forwarding ------------------

def test_calib_argv_pure_function(monkeypatch):
    monkeypatch.delenv("PREDICT_CALIB_MANIFEST", raising=False)
    monkeypatch.delenv("PREDICT_CALIB_MODE", raising=False)
    assert runner_mod._calib_argv("PREDICT") == []                      # default off
    monkeypatch.setenv("PREDICT_CALIB_MANIFEST", _MANIFEST)             # path but legacy mode
    assert runner_mod._calib_argv("PREDICT") == []                      # still off (needs mode)
    monkeypatch.setenv("PREDICT_CALIB_MODE", "manifest-required")
    assert runner_mod._calib_argv("PREDICT") == [
        "--calib-manifest", _MANIFEST, "--calib-mode", "manifest-required"]


def test_serving_predict_forwards_calib_env(monkeypatch):
    monkeypatch.setenv("PREDICT_CALIB_MANIFEST", _MANIFEST)
    monkeypatch.setenv("PREDICT_CALIB_MODE", "manifest-required")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _proc()

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    runner_mod._serving_predict("202501050101")
    assert "--calib-manifest" in seen["cmd"] and _MANIFEST in seen["cmd"]
    assert "manifest-required" in seen["cmd"]


def test_betting_recommend_forwards_calib_env_and_default_is_clean(monkeypatch):
    # default (no env) → no calib flags (backward-compat)
    monkeypatch.delenv("RECOMMEND_CALIB_MANIFEST", raising=False)
    monkeypatch.delenv("RECOMMEND_CALIB_MODE", raising=False)
    seen = {}
    monkeypatch.setattr(runner_mod.subprocess, "run",
                        lambda cmd, **kw: seen.__setitem__("cmd", cmd) or _proc())
    runner_mod._betting_recommend("202501050101")
    assert "--calib-manifest" not in seen["cmd"]
    # env set → forwarded
    monkeypatch.setenv("RECOMMEND_CALIB_MANIFEST", _MANIFEST)
    monkeypatch.setenv("RECOMMEND_CALIB_MODE", "manifest-required")
    runner_mod._betting_recommend("202501050101")
    assert "--calib-manifest" in seen["cmd"] and "manifest-required" in seen["cmd"]
