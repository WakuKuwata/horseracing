"""Feature 086 SC-009: a 429 suppresses HTTP on the next real predict capture."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosSnapshot, Horse, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_ops import runner as runner_mod
from horseracing_ops.enqueue import enqueue_predict

pytestmark = pytest.mark.integration

RID = "202607280101"


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append(self.path)
        if self.path == "/robots.txt":
            status = 200
            body = b"User-agent: *\nAllow: /\n"
        elif self.path.startswith("/odds"):
            if self.server.odds_delay_s:
                time.sleep(self.server.odds_delay_s)
            status = 429 if self.server.odds_requests == 0 else 200
            self.server.odds_requests += 1
            body = json.dumps(
                {
                    "data": {
                        "odds": {
                            "1": {
                                f"{number:02d}": [
                                    str(2.0 + number),
                                    "0.0",
                                    str(number),
                                ]
                                for number in range(1, 5)
                            }
                        }
                    }
                }
            ).encode()
        else:
            status = 404
            body = b"not found"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args) -> None:
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *, odds_delay_s: float = 0.0):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[str] = []
        self.odds_requests = 0
        self.odds_delay_s = odds_delay_s


@contextmanager
def _server(*, odds_delay_s: float = 0.0):
    server = _Server(odds_delay_s=odds_delay_s)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _seed_capture_race(session) -> None:
    now = datetime.datetime.now(datetime.UTC)
    session.add(
        Race(
            race_id=RID,
            race_number=1,
            race_date=now.date(),
            post_time=now + datetime.timedelta(hours=1),
        )
    )
    for number in range(1, 5):
        horse_id = f"cooldown-H{number}"
        session.add(Horse(horse_id=horse_id, horse_name=horse_id))
        session.add(
            RaceHorse(
                race_id=RID,
                horse_id=horse_id,
                horse_number=number,
                entry_status=EntryStatus.STARTED,
            )
        )
    session.commit()


def _serving_ok(_race_id: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="no races inferred",
        stderr="",
    )


def _install_sitecustomize(monkeypatch, tmp_path, base_url: str) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        "import os\n"
        "import horseracing_scrape.urls as urls\n"
        "urls._ODDS_API = os.environ['CAPTURE_TEST_BASE_URL'] + '/odds'\n",
        encoding="utf-8",
    )
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = str(tmp_path)
    if existing_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{existing_pythonpath}"
    monkeypatch.setenv("PYTHONPATH", pythonpath)
    monkeypatch.setenv("CAPTURE_TEST_BASE_URL", base_url)


def test_second_predict_after_429_issues_zero_http(
    session,
    monkeypatch,
    tmp_path,
):
    _seed_capture_race(session)
    monkeypatch.setattr(runner_mod, "_serving_predict", _serving_ok)

    with _server() as (base_url, server):
        _install_sitecustomize(monkeypatch, tmp_path, base_url)

        first, _ = enqueue_predict(session, RID, origin="manual_ui")
        session.commit()
        runner_mod.run_predict(session, first)
        requests_after_first = list(server.requests)

        second, _ = enqueue_predict(session, RID, origin="manual_ui")
        session.commit()
        runner_mod.run_predict(session, second)

    assert requests_after_first[0] == "/robots.txt"
    assert requests_after_first[1].startswith("/odds?")
    # The first odds 429 installs a domain cooldown. The next predict is
    # refused before even sending robots.txt: no additional HTTP.
    assert server.requests == requests_after_first
    assert first.summary["capture"]["reason"] == "source_cooldown"
    assert second.summary["capture"]["reason"] == "source_cooldown"


def test_outer_timeout_is_done_no_refetch_and_no_late_snapshot(
    session,
    monkeypatch,
    tmp_path,
):
    _seed_capture_race(session)
    monkeypatch.setattr(runner_mod, "_serving_predict", _serving_ok)
    # 3.5s, not 2.0s: this test needs the ODDS request to be IN FLIGHT when the outer
    # timeout fires. Before it can start, the capture must pay ~1.2s of `uv run` startup
    # (measured, docs/plan/086-capture-timing.md) plus the politeness layer's mandatory
    # 1.0s gap after robots.txt -- ~2.2s total. A 2.0s cutoff fired before the request
    # existed, so the test was asserting a state it had made unreachable.
    monkeypatch.setattr(runner_mod, "_CAPTURE_TIMEOUT_S", 3.5)

    with _server(odds_delay_s=4.0) as (base_url, server):
        _install_sitecustomize(monkeypatch, tmp_path, base_url)
        job, _ = enqueue_predict(session, RID, origin="manual_ui")
        session.commit()

        runner_mod.run_predict(session, job)
        requests_at_timeout = list(server.requests)
        count_at_timeout = session.scalar(
            select(func.count()).select_from(ChaosSnapshot)
        )

        runner_mod.run_predict(session, job)
        time.sleep(4.2)
        count_after_wait = session.scalar(
            select(func.count()).select_from(ChaosSnapshot)
        )

    assert job.summary["capture"] == {
        "state": "done",
        "outcome": "unknown",
        "reason": "outer_timeout",
    }
    assert requests_at_timeout[0] == "/robots.txt"
    assert requests_at_timeout[1].startswith("/odds?")
    assert server.requests == requests_at_timeout
    assert count_at_timeout == 0
    assert count_after_wait == 0
