"""Feature 137: 期待回収率 recompute job — enqueue dedup, worker mapping, refresh trigger.

`_training_market_ev` (the training-CLI subprocess) is monkeypatched — ops must not import the
training stack (boundary II/VI). The CLI contract (plan.md 0.3): final stdout line ``OK: …`` or
``SKIPPED: …``, non-zero exit on failure. One job = one race date (scope="date").
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.urls import entries_url, result_url, win_odds_url
from sqlalchemy import select

from horseracing_ops import JOB_TYPE_EXPECTED_RETURN
from horseracing_ops import config as config_mod
from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_expected_return, enqueue_race
from horseracing_ops.worker import _CPU_LANE, _IO_LANE, claim_one, drain, release_inflight
from tests._synth import mark_finished, seed_race
from tests.conftest import REAL_RID, _read

pytestmark = pytest.mark.integration

DAY = datetime.date(2024, 12, 28)  # REAL_RID's date (seed_race default)
OTHER_DAY = datetime.date(2024, 12, 29)

#: the real launcher, captured at import — before conftest's autouse guard replaces it for each
#: test. The argv tests call it with subprocess.Popen faked out, so nothing is ever run.
_REAL_LAUNCHER = runner_mod._training_market_ev


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _er_jobs(session) -> list[IngestionJob]:
    session.expire_all()
    return list(session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type == JOB_TYPE_EXPECTED_RETURN)
        .order_by(IngestionJob.created_at.asc(), IngestionJob.ingestion_job_id.asc())
    ))


def _pending_pages(*, with_odds: bool = True) -> dict[str, str]:
    """No result page → the race stays result-pending after the refresh."""
    pages = {entries_url(REAL_RID): _read(f"entries_{REAL_RID}.html")}
    if with_odds:
        pages[win_odds_url(REAL_RID)] = _read(f"odds_{REAL_RID}.json")
    return pages


def _settled_pages() -> dict[str, str]:
    return {
        **_pending_pages(),
        result_url(REAL_RID): _read(f"results_{REAL_RID}.html"),
    }


def _no_quotes(monkeypatch) -> None:
    # the exotic price grid has no fixture for REAL_RID; keep the pending-race refresh focused
    monkeypatch.setattr(runner_mod, "CONFIG", replace(runner_mod.CONFIG,
                                                      exotic_quote_bet_types=()))


# --- enqueue ------------------------------------------------------------------------------------

def test_enqueue_creates_a_date_scoped_job(session):
    job, reused = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    assert reused is False
    assert job.job_type == JOB_TYPE_EXPECTED_RETURN
    assert job.scope == "date" and job.scope_value == "2024-12-28"
    assert job.status == JobStatus.QUEUED
    assert job.summary == {"kind": "expected_return", "source": "auto_after_refresh"}


def test_queued_job_is_reused_per_date(session):
    first, r1 = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    again, r2 = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    other, r3 = enqueue_expected_return(session, OTHER_DAY, source="auto_after_refresh")
    session.commit()
    assert (r1, r2, r3) == (False, True, False)
    assert again.ingestion_job_id == first.ingestion_job_id
    assert other.ingestion_job_id != first.ingestion_job_id
    assert len(_er_jobs(session)) == 2


def test_running_job_is_not_reused(session):
    """A RUNNING job may already have read older odds — a new trigger must queue a fresh job."""
    first, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    claimed = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert claimed.ingestion_job_id == first.ingestion_job_id
    assert claimed.status == JobStatus.RUNNING

    second, reused = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    release_inflight(claimed.ingestion_job_id)
    assert reused is False
    assert second.ingestion_job_id != first.ingestion_job_id
    assert second.status == JobStatus.QUEUED


def test_completed_job_is_not_reused(session, monkeypatch):
    monkeypatch.setattr(runner_mod, "_training_market_ev",
                        lambda d: _proc(0, "OK: races=1 horses=18 from=x to=x"))
    first, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    assert drain(session) == 1
    second, reused = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    assert reused is False and second.ingestion_job_id != first.ingestion_job_id


def test_manual_trigger_promotes_a_queued_auto_job(session):
    auto, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    promoted, reused = enqueue_expected_return(session, DAY, source="manual_ui")
    session.commit()
    assert reused and promoted.ingestion_job_id == auto.ingestion_job_id
    assert promoted.summary["source"] == "manual_ui"
    # an automatic trigger never demotes a manual one
    again, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    assert again.summary["source"] == "manual_ui"


def test_unknown_source_is_rejected(session):
    with pytest.raises(ValueError, match="unsupported expected_return source"):
        enqueue_expected_return(session, DAY, source="manual")


# --- worker mapping -----------------------------------------------------------------------------

def test_ok_marker_is_succeeded(session, monkeypatch, client):
    seen = []

    def fake(race_date):
        seen.append(race_date)
        return _proc(0, "loading rows\nOK: races=12 horses=180 from=2024-12-28 to=2024-12-28\n")

    monkeypatch.setattr(runner_mod, "_training_market_ev", fake)
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    assert drain(session) == 1
    session.refresh(job)

    assert seen == ["2024-12-28"]
    assert job.status == JobStatus.SUCCEEDED
    assert job.retry_count == 0 and job.completed_at is not None
    assert job.processed_rows == 180
    assert job.summary["kind"] == "expected_return"
    assert job.summary["source"] == "auto_after_refresh"  # enqueue-time label survives
    assert job.summary["race_date"] == "2024-12-28"
    assert job.summary["model_dir"] == runner_mod.CONFIG.market_ev_model_dir
    assert job.summary["ensemble_dir"] == runner_mod.CONFIG.market_ev_ensemble_dir  # 138 audit
    assert job.summary["result"] == {"races": 12, "horses": 180,
                                     "from": "2024-12-28", "to": "2024-12-28"}
    assert "checkpoints_error" not in job.summary
    body = client.get(f"/ops/v1/jobs/{job.ingestion_job_id}").json()
    assert body["status"] == "succeeded" and body["kind"] == "expected_return"


@pytest.mark.parametrize("checkpoints", ["ok", "pending"])
def test_two_version_marker_is_carried_into_the_result(session, monkeypatch, checkpoints):
    """Feature 138: the ``--ensemble-dir`` suffix lands in the result like the other pairs."""
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0, "OK: races=12 horses=180 from=2024-12-28 to=2024-12-28 "
           f"versions=2 picks=3 checkpoints={checkpoints}\n"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)

    assert job.status == JobStatus.SUCCEEDED and job.processed_rows == 180
    assert job.summary["result"] == {"races": 12, "horses": 180,
                                     "from": "2024-12-28", "to": "2024-12-28",
                                     "versions": 2, "picks": 3, "checkpoints": checkpoints}
    assert "checkpoints_error" not in job.summary


def test_checkpoint_error_keeps_the_job_succeeded_and_records_the_cause(
        session, monkeypatch, client):
    """Feature 138: the judgement runs after both versions and the picks are committed, so its
    failure must not fail the job — but the cause (the ``attention-checkpoints:`` line printed
    before the marker) must stay in the summary, not vanish into an ok-looking SUCCEEDED."""
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0,
        "loading rows\n"
        "attention-checkpoints: error=OperationalError: lock timeout\n"
        "OK: races=2 horses=30 from=2024-12-28 to=2024-12-28 "
        "versions=2 picks=0 checkpoints=error\n",
        "some stderr noise"))
    job, _ = enqueue_expected_return(session, DAY, source="manual_ui")
    session.commit()
    drain(session)
    session.refresh(job)

    assert job.status == JobStatus.SUCCEEDED and job.retry_count == 0
    # the jobs pages show error_message / error_count, never the summary
    assert job.error_message == "checkpoints: error=OperationalError: lock timeout"
    assert job.error_count == 1
    assert job.processed_rows == 30
    assert job.summary["result"]["checkpoints"] == "error"
    assert job.summary["result"]["picks"] == 0 and job.summary["result"]["versions"] == 2
    assert job.summary["checkpoints_error"] == "error=OperationalError: lock timeout"
    assert job.summary["ensemble_dir"] == runner_mod.CONFIG.market_ev_ensemble_dir
    body = client.get(f"/ops/v1/jobs/{job.ingestion_job_id}").json()
    assert body["status"] == "succeeded"
    assert body["error_count"] == 1
    assert body["error_message"] == "checkpoints: error=OperationalError: lock timeout"


@pytest.mark.parametrize("checkpoints", ["ok", "pending"])
def test_no_checkpoint_error_leaves_the_error_fields_empty(session, monkeypatch, checkpoints):
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0, f"OK: races=1 horses=1 from=x to=x versions=2 picks=1 checkpoints={checkpoints}\n",
        "some stderr noise"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.SUCCEEDED
    assert job.error_message is None and job.error_count is None


def test_checkpoint_error_without_a_cause_line_falls_back_to_the_tail(session, monkeypatch):
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0, "OK: races=1 horses=1 from=x to=x versions=2 picks=1 checkpoints=error\n",
        "Traceback: judge blew up"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.SUCCEEDED
    assert "judge blew up" in job.summary["checkpoints_error"]
    assert "judge blew up" in job.error_message and job.error_count == 1


def test_checkpoint_error_fallback_prefers_stderr_over_a_long_stdout(session, monkeypatch):
    """A diagnostics line on stdout longer than 500 chars must not push the stderr cause out of
    the recorded error (the combined tail puts stdout last)."""
    boosters = "market-ev: model_version=mev-binary-v2 boosters=" + ",".join(
        f"/abs/artifacts/market_ev/mev-ens15-v1/seed{i:02d}/booster.txt" for i in range(15))
    assert len(boosters) > 500
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0,
        f"{boosters}\n"
        "OK: races=1 horses=1 from=x to=x versions=2 picks=1 checkpoints=error\n",
        "Traceback (most recent call last):\nRuntimeError: judge blew up"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.SUCCEEDED
    assert "RuntimeError: judge blew up" in job.summary["checkpoints_error"]
    assert "boosters=" not in job.summary["checkpoints_error"]
    assert "RuntimeError: judge blew up" in job.error_message


def test_checkpoint_error_without_any_stderr_falls_back_to_the_combined_tail(
        session, monkeypatch):
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0, "judge blew up on stdout\n"
           "OK: races=1 horses=1 from=x to=x versions=2 picks=1 checkpoints=error\n", ""))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert "judge blew up on stdout" in job.summary["checkpoints_error"]


def test_only_the_error_form_of_the_checkpoints_line_is_the_cause(session, monkeypatch):
    """The standalone judgement prints ``attention-checkpoints: written=… pending=…`` summaries
    too; one printed after the error line must not replace the cause."""
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0,
        "attention-checkpoints: error=OperationalError: lock timeout\n"
        "attention-checkpoints: written=0 pending=10\n"
        "OK: races=1 horses=1 from=x to=x versions=2 picks=1 checkpoints=error\n",
        ""))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.summary["checkpoints_error"] == "error=OperationalError: lock timeout"
    assert job.error_message == "checkpoints: error=OperationalError: lock timeout"


def test_a_non_error_checkpoints_line_alone_is_not_taken_as_the_cause(session, monkeypatch):
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(
        0,
        "attention-checkpoints: written=0 pending=10\n"
        "OK: races=1 horses=1 from=x to=x versions=2 picks=1 checkpoints=error\n",
        "Traceback: judge blew up"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert "judge blew up" in job.summary["checkpoints_error"]
    assert "written=0" not in job.summary["checkpoints_error"]


def test_skipped_marker_is_skipped_with_reason(session, monkeypatch, client):
    monkeypatch.setattr(runner_mod, "_training_market_ev",
                        lambda d: _proc(0, "SKIPPED: no_races_with_odds\n"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.SKIPPED
    assert job.summary["reason"] == "no_races_with_odds"
    body = client.get(f"/ops/v1/jobs/{job.ingestion_job_id}").json()
    assert body["status"] == "skipped" and body["reason"] == "no_races_with_odds"


def test_nonzero_exit_is_failed_without_retry(session, monkeypatch):
    monkeypatch.setattr(runner_mod, "_training_market_ev",
                        lambda d: _proc(1, "OK: races=1 horses=1 from=x to=x", "Traceback: boom"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.retry_count == 0  # deterministic failure, not worker-retried
    assert "boom" in job.error_message
    assert "boom" in job.summary["error"]


def test_exit_zero_without_marker_is_failed(session, monkeypatch):
    """Success is claimed only when the CLI says so (fail-closed on a broken contract)."""
    monkeypatch.setattr(runner_mod, "_training_market_ev", lambda d: _proc(0, "done\n"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "marker" in job.error_message


def test_marker_must_be_the_final_line(session, monkeypatch):
    monkeypatch.setattr(runner_mod, "_training_market_ev",
                        lambda d: _proc(0, "OK: races=1 horses=1 from=x to=x\nSKIPPED: late\n"))
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.SKIPPED and job.summary["reason"] == "late"


def test_timeout_is_failed_without_retry(session, monkeypatch):
    def fake(race_date):
        raise subprocess.TimeoutExpired(cmd=["uv"], timeout=runner_mod._MARKET_EV_TIMEOUT_S)

    monkeypatch.setattr(runner_mod, "_training_market_ev", fake)
    job, _ = enqueue_expected_return(session, DAY, source="auto_after_refresh")
    session.commit()
    drain(session)
    session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.retry_count == 0
    assert "timed out" in job.error_message


# --- the subprocess launcher (argv captured, nothing run) ----------------------------------------

class _FakePopen:
    pid = 13579
    returncode = 0

    def __init__(self, *, timeout: bool = False):
        self.timeout = timeout
        self.communicate_timeout = None

    def communicate(self, *, timeout):
        self.communicate_timeout = timeout
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd=["uv"], timeout=timeout)
        return ("OK: races=1 horses=2 from=a to=a versions=2 picks=0 checkpoints=ok\n", "")


def test_launcher_argv_cwd_env_and_timeout(monkeypatch):
    seen: dict = {}
    fake = _FakePopen()

    def popen(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        return fake

    monkeypatch.setattr(runner_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(runner_mod, "owner_database_url", lambda: "postgresql+psycopg://o/db")
    monkeypatch.setattr(runner_mod, "CONFIG", replace(runner_mod.CONFIG,
                                                      market_ev_model_dir="/abs/models/mev",
                                                      market_ev_ensemble_dir="/abs/models/ens"))
    monkeypatch.setenv("VIRTUAL_ENV", "/ops/.venv")

    result = _REAL_LAUNCHER("2024-12-28")

    training_dir = str(Path(runner_mod.__file__).resolve().parents[3] / "training")
    assert seen["cmd"] == [
        "uv", "run", "--project", training_dir, "python", "-m", "horseracing_training",
        "market-ev", "--date", "2024-12-28", "--pending-only", "--model-dir", "/abs/models/mev",
        "--ensemble-dir", "/abs/models/ens",  # 138: right after --model-dir
        "--database-url", "postgresql+psycopg://o/db",
    ]
    assert seen["kwargs"]["cwd"] == training_dir
    assert "VIRTUAL_ENV" not in seen["kwargs"]["env"]
    assert seen["kwargs"]["start_new_session"] is True
    assert fake.communicate_timeout == runner_mod._MARKET_EV_TIMEOUT_S == 600
    assert result.returncode == 0 and result.stdout.startswith("OK:")


def test_launcher_timeout_kills_the_process_group(monkeypatch):
    fake = _FakePopen(timeout=True)
    killed = []
    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda cmd, **kw: fake)
    monkeypatch.setattr(runner_mod, "owner_database_url", lambda: "postgresql+psycopg://o/db")
    monkeypatch.setattr(runner_mod, "_kill_capture_process_group", killed.append)

    with pytest.raises(subprocess.TimeoutExpired):
        _REAL_LAUNCHER("2024-12-28")
    assert killed == [fake]


# --- config -------------------------------------------------------------------------------------

def test_config_defaults():
    default = config_mod._DEFAULT_MARKET_EV_MODEL_DIR
    assert default.is_absolute()  # the training CLI rejects relative model dirs
    assert default.parts[-3:] == ("artifacts", "market_ev", "mev-binary-v2")
    assert (default.parents[2] / "ops" / "src" / "horseracing_ops").is_dir()  # = repo root
    if "OPS_EXPECTED_RETURN_ON_REFRESH" not in os.environ:
        assert config_mod.CONFIG.expected_return_on_refresh is True  # default ON
    if "OPS_MARKET_EV_MODEL_DIR" not in os.environ:
        assert config_mod.CONFIG.market_ev_model_dir == str(default)

    ensemble = config_mod._DEFAULT_MARKET_EV_ENSEMBLE_DIR  # Feature 138
    assert ensemble.is_absolute()
    assert ensemble.parts[-3:] == ("artifacts", "market_ev", "mev-ens15-v1")
    assert ensemble.parent == default.parent
    if "OPS_MARKET_EV_ENSEMBLE_DIR" not in os.environ:
        assert config_mod.CONFIG.market_ev_ensemble_dir == str(ensemble)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, True), ("", True), ("1", True), ("on", True),
     ("0", False), ("false", False), ("OFF", False), ("no", False)],
)
def test_expected_return_switch_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("OPS_EXPECTED_RETURN_ON_REFRESH", raising=False)
    else:
        monkeypatch.setenv("OPS_EXPECTED_RETURN_ON_REFRESH", raw)
    assert config_mod._bool("OPS_EXPECTED_RETURN_ON_REFRESH", True) is expected


def test_model_dir_env_override(monkeypatch):
    monkeypatch.setenv("OPS_MARKET_EV_MODEL_DIR", " /abs/elsewhere ")
    assert config_mod._str("OPS_MARKET_EV_MODEL_DIR", "d") == "/abs/elsewhere"
    monkeypatch.setenv("OPS_MARKET_EV_MODEL_DIR", "")
    assert config_mod._str("OPS_MARKET_EV_MODEL_DIR", "d") == "d"


def test_ensemble_dir_env_override(monkeypatch):
    """Feature 138: OPS_MARKET_EV_ENSEMBLE_DIR is read when the config is built (import time), so
    load a private copy of the module — the shared CONFIG the runner holds stays untouched."""
    monkeypatch.setenv("OPS_MARKET_EV_ENSEMBLE_DIR", " /abs/ens-elsewhere ")
    spec = importlib.util.spec_from_file_location("_ops_config_copy", config_mod.__file__)
    copy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, copy)  # @dataclass resolves its module here
    spec.loader.exec_module(copy)
    assert copy.CONFIG.market_ev_ensemble_dir == "/abs/ens-elsewhere"


# --- the refresh trigger ------------------------------------------------------------------------

def test_pending_refresh_with_odds_enqueues_exactly_one_date_job(session, monkeypatch):
    _no_quotes(monkeypatch)
    seed_race(session, race_id=REAL_RID)
    job, _ = enqueue_race(session, REAL_RID, origin="daily_bulk")
    session.commit()

    drain(session, fetcher=FixtureFetcher(_pending_pages()), max_jobs=1,
          job_types=_IO_LANE)  # the refresh only

    session.refresh(job)
    odds_calls = [c for c in job.summary["calls"] if c["job_type"] == "odds"]
    assert odds_calls and odds_calls[0]["written"] > 0
    ers = _er_jobs(session)
    assert len(ers) == 1
    er = ers[0]
    assert er.status == JobStatus.QUEUED
    assert er.scope == "date" and er.scope_value == "2024-12-28"
    assert er.summary["source"] == "auto_after_refresh"
    assert job.summary["expected_return_job_id"] == str(er.ingestion_job_id)
    # the front follows the recompute through the job poll endpoint (followup_job_id)
    from horseracing_ops.routers.jobs import _to_job

    assert str(_to_job(job).followup_job_id) == str(er.ingestion_job_id)


def test_second_refresh_of_the_day_reuses_the_queued_job(session, monkeypatch):
    _no_quotes(monkeypatch)
    seed_race(session, race_id=REAL_RID)
    enqueue_race(session, REAL_RID, origin="daily_bulk")
    session.commit()
    drain(session, fetcher=FixtureFetcher(_pending_pages()), max_jobs=1,
          job_types=_IO_LANE)
    enqueue_race(session, REAL_RID, origin="manual_ui", force=True)
    session.commit()
    drain(session, fetcher=FixtureFetcher(_pending_pages()), max_jobs=1,
          job_types=_IO_LANE)

    ers = _er_jobs(session)
    assert len(ers) == 1
    assert ers[0].summary["source"] == "manual_ui"  # the click promoted it


def test_manual_refresh_marks_the_job_manual(session, monkeypatch):
    _no_quotes(monkeypatch)
    seed_race(session, race_id=REAL_RID)
    enqueue_race(session, REAL_RID, origin="manual_ui")
    session.commit()
    drain(session, fetcher=FixtureFetcher(_pending_pages()), max_jobs=1,
          job_types=_IO_LANE)
    ers = _er_jobs(session)
    assert len(ers) == 1 and ers[0].summary["source"] == "manual_ui"


def test_triggered_job_runs_through_the_worker(session, monkeypatch):
    _no_quotes(monkeypatch)
    seen = []
    monkeypatch.setattr(runner_mod, "_training_market_ev",
                        lambda d: seen.append(d) or _proc(0, "OK: races=1 horses=18 from=a to=a"))
    seed_race(session, race_id=REAL_RID)
    enqueue_race(session, REAL_RID, origin="daily_bulk")
    session.commit()

    drain(session, fetcher=FixtureFetcher(_pending_pages()))  # refresh + its follow-ups

    assert seen == ["2024-12-28"]
    (er,) = _er_jobs(session)
    assert er.status == JobStatus.SUCCEEDED


def test_settled_race_does_not_trigger(session, monkeypatch):
    """The result page landed in this pass → settled → no recompute."""
    _no_quotes(monkeypatch)
    seed_race(session, race_id=REAL_RID)
    job, _ = enqueue_race(session, REAL_RID, origin="manual_ui")
    session.commit()
    drain(session, fetcher=FixtureFetcher(_settled_pages()), max_jobs=1,
          job_types=_IO_LANE)
    session.refresh(job)
    assert _er_jobs(session) == []
    assert "expected_return_job_id" not in job.summary


def test_corner_backfill_does_not_trigger(session, monkeypatch):
    _no_quotes(monkeypatch)
    seed_race(session, race_id=REAL_RID)
    mark_finished(session, race_id=REAL_RID)
    enqueue_race(session, REAL_RID, origin="corner_backfill")
    session.commit()
    drain(session, fetcher=FixtureFetcher(_settled_pages()), max_jobs=1,
          job_types=_IO_LANE)
    assert _er_jobs(session) == []


def test_corner_backfill_origin_is_excluded_even_if_odds_were_written(session):
    """The gate itself, independent of the race state a patch-up happens to find."""
    seed_race(session, race_id=REAL_RID)  # pending
    wrote = SimpleNamespace(written=5)
    assert runner_mod._maybe_enqueue_expected_return(
        session, REAL_RID, odds=wrote, refresh_origin="corner_backfill") is None
    assert runner_mod._maybe_enqueue_expected_return(
        session, REAL_RID, odds=None, refresh_origin="daily_bulk") is None
    assert runner_mod._maybe_enqueue_expected_return(
        session, REAL_RID, odds=SimpleNamespace(written=0), refresh_origin="daily_bulk") is None
    assert _er_jobs(session) == []
    assert runner_mod._maybe_enqueue_expected_return(
        session, REAL_RID, odds=wrote, refresh_origin="daily_bulk") is not None
    session.commit()
    assert len(_er_jobs(session)) == 1


def test_no_odds_written_does_not_trigger(session, monkeypatch):
    """Odds page unavailable → odds sub-step wrote nothing → the stored value is not stale."""
    _no_quotes(monkeypatch)
    seed_race(session, race_id=REAL_RID)
    job, _ = enqueue_race(session, REAL_RID, origin="daily_bulk")
    session.commit()
    drain(session, fetcher=FixtureFetcher(_pending_pages(with_odds=False)), max_jobs=1,
          job_types=_IO_LANE)
    session.refresh(job)
    odds_calls = [c for c in job.summary["calls"] if c["job_type"] == "odds"]
    assert odds_calls and odds_calls[0]["written"] == 0
    assert _er_jobs(session) == []


def test_switch_off_does_not_trigger(session, monkeypatch):
    monkeypatch.setattr(runner_mod, "CONFIG", replace(runner_mod.CONFIG,
                                                      exotic_quote_bet_types=(),
                                                      expected_return_on_refresh=False))
    seed_race(session, race_id=REAL_RID)
    job, _ = enqueue_race(session, REAL_RID, origin="daily_bulk")
    session.commit()
    drain(session, fetcher=FixtureFetcher(_pending_pages()), max_jobs=1,
          job_types=_IO_LANE)
    session.refresh(job)
    assert [c["written"] for c in job.summary["calls"] if c["job_type"] == "odds"][0] > 0
    assert _er_jobs(session) == []
